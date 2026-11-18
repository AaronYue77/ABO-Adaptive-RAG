"""Session transcript and lifecycle records shared by the ABO entrypoints."""

from time import perf_counter

from audit import append_result
from common import WORKSPACE, append_log, append_log_json, issue, operation, timestamp, write_json
from log_privacy import redact_log

TRANSCRIPT = "对话记录.txt"
RUN_LOG = "运行记录.txt"
EVENTS = "运行事件.jsonl"


def transcript_turn(row):
    strategy = (row.get("adaptive") or {}).get("strategy", "未标注")
    status = row.get("status", "ok")
    sources = list(dict.fromkeys(source["label"] for source in row.get("display_sources", [])))
    references = row.get("citations", [])
    lines = [
        f"[{row.get('timestamp') or timestamp()}] {row.get('question_id', '未完成请求')}",
        f"用户：{row.get('question', '')}",
        f"助手：{row.get('display_answer', row.get('answer', ''))}",
        f"结果：{row.get('decision', row.get('action', 'error'))} · {status} · 策略 {strategy}",
        f"来源：{'；'.join(sources or references) or '无'}",
    ]
    if row.get("issue_id"):
        lines.append(f"关联问题：{row['issue_id']}")
    if row.get("thread_id"):
        lines.append(f"对话线程：{row['thread_id']}")
    return "\n" + "\n".join(lines) + "\n"


class SessionJournal:
    def __init__(self, output, entry):
        self.output = output
        self.entry = entry
        self.started = perf_counter()
        self.turns = 0
        self.failures = 0
        self.closed = False
        self.issue_ids = set()
        self.repeated_issues = {}
        self.event("start", "开始", "创建独立会话目录")
        operation("启动 ABO 会话", entry, "会话目录已建立", [output], "入口正在初始化", "开始")

    def event(self, kind, status, detail, issue_id=None):
        stamp = timestamp()
        payload = {
            "timestamp": stamp,
            "session": self.output.name,
            "entry": self.entry,
            "event": kind,
            "status": status,
            "detail": detail,
            "issue_id": issue_id,
        }
        append_log(
            self.output / RUN_LOG,
            f"[{stamp}] {self.entry} · {kind} · {status}\n{detail}\n"
            + (f"关联问题：{issue_id}\n" if issue_id else ""),
        )
        append_log_json(self.output / EVENTS, payload)

    def ready(self, mode, config_sha=None):
        append_log(
            self.output / TRANSCRIPT, f"ABO 对话记录 · {self.output.name}\n入口：{self.entry}\n"
        )
        self.event("ready", "就绪", f"运行方式 {mode}，配置 SHA256 {config_sha or '见逐题记录'}")

    def attach_issue(self, row):
        if row.get("issue_id"):
            return row["issue_id"]
        faults = [str(row["error"])] if row.get("error") else []
        faults.extend(str(value) for value in row.get("errors") or [])
        faults.extend(
            f"{call.get('stage', '模型')}：{call.get('error') or call.get('status')}"
            for call in row.get("model_calls", [])
            if call.get("status") == "error"
        )
        if not faults and row.get("status") != "error" and row.get("decision") != "unavailable":
            return None
        detail = redact_log("；".join(faults) or "本轮执行失败，未得到可用答案")
        key = self.repeated_issues.get(detail)
        if not key:
            key = issue(
                "ABO 会话执行异常",
                detail,
                "保留回退结果与逐题记录，检查异常环节。",
                str(self.output / "逐题结果.txt"),
            )
            self.repeated_issues[detail] = key
        return key

    def turn(self, row):
        self.turns += 1
        append_log(self.output / TRANSCRIPT, transcript_turn(row))
        key = row.get("issue_id")
        if key:
            self.issue_ids.add(key)
            failed = row.get("status") == "error" or row.get("decision") == "unavailable"
            self.failures += int(failed)
            self.event(
                "turn",
                "失败" if failed else "含异常回退",
                f"{row.get('question_id')} · {row.get('decision', row.get('action'))}",
                key,
            )

    def failed(self, phase, error, question=None):
        detail = redact_log(f"{type(error).__name__}：{error}")
        key = issue(
            f"ABO {phase}失败",
            detail,
            "从运行记录定位，修复后在同一编号追加实际复验。",
            str(self.output / RUN_LOG),
        )
        self.issue_ids.add(key)
        self.failures += 1
        self.event(phase, "失败", detail, key)
        if question is not None:
            append_log(
                self.output / TRANSCRIPT,
                transcript_turn(
                    {
                        "question": question,
                        "answer": "本轮未完成，详见运行记录。",
                        "status": "error",
                        "issue_id": key,
                    }
                ),
            )
        return key

    def mirror(self, row):
        """Keep the complete remote evidence row with an explicit origin."""
        local = {
            **row,
            "origin_run_name": row.get("run_name"),
            "origin_question_id": row.get("question_id"),
            "run_name": self.output.name,
            "run_directory": str(self.output.relative_to(WORKSPACE)),
        }
        if local.get("issue_id"):
            local["issue_id"] = "Kaggle " + local["issue_id"]
        append_result(self.output, local)
        self.turn(local)

    def remote_config(self, config):
        write_json(self.output / "config.json", redact_log(config))

    def close(self):
        if self.closed:
            return
        self.closed = True
        summary = f"已记录 {self.turns} 轮结果，失败 {self.failures} 次，累计 {perf_counter()-self.started:.2f} 秒"
        status = "结束，含失败" if self.failures else "结束"
        self.event("close", status, summary)
        operation(
            "结束 ABO 会话",
            self.entry,
            summary,
            [self.output],
            "查看会话逐题和运行日志",
            status,
            "、".join(sorted(self.issue_ids)) or "无",
        )
