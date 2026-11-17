const byId = (id) => document.getElementById(id);
const number = new Intl.NumberFormat('zh-CN');
const storageKey = 'abo-catalogue-chats-v1';
const pageSize = 24;
const questionBox = byId('question');
const conversation = byId('conversation');
const evidence = byId('evidence');
const grid = byId('browse-grid');
const scroller = byId('browse-scroll');
const filter = byId('filter-categories');

function make(tag, content = '', className = '') {
  const result = document.createElement(tag);
  result.textContent = content;
  result.className = className;
  return result;
}

function button(content, className, run) {
  const result = make('button', content, className);
  result.type = 'button';
  result.addEventListener('click', run);
  return result;
}

function showStatus(id, message) {
  const status = byId(id);
  status.textContent = message;
  status.hidden = !message;
}

async function request(path, options = {}) {
  const response = await fetch(`/api/v1${path}`, {cache: 'no-store', ...options});
  if (response.status === 204) return null;
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = typeof body.detail === 'string' ? body.detail : '请求未完成，请重试。';
    throw Object.assign(new Error(message), {status: response.status});
  }
  return body;
}

function post(path, body) {
  return request(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
}

function externalLink(url, label) {
  if (!url) return null;
  try {
    const address = new URL(url);
    if (address.protocol !== 'https:') return null;
    const link = make('a', label);
    link.href = address.href;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    return link;
  } catch {
    return null;
  }
}

function picture(url, eager = false) {
  const frame = make('div', '', 'item-image');
  const unavailable = make('span', url ? '图片暂不可用' : '暂无原图');
  if (!url) {
    frame.append(unavailable);
    return frame;
  }
  const image = make('img');
  image.alt = '';
  image.loading = eager ? 'eager' : 'lazy';
  image.decoding = 'async';
  image.referrerPolicy = 'no-referrer';
  image.addEventListener('error', () => image.replaceWith(unavailable), {once: true});
  image.src = url;
  frame.append(image);
  return frame;
}

function card(title, subtitle, image, className, run) {
  const item = button('', `item-card ${className}`, run);
  item.append(picture(image), make('span', title, 'item-title'), make('span', subtitle, 'item-meta'));
  return item;
}

function historyFromStorage() {
  try {
    const previous = JSON.parse(localStorage.getItem(storageKey) || '[]');
    return Array.isArray(previous) ? previous.filter((item) => item?.id && Array.isArray(item.messages)) : [];
  } catch {
    return [];
  }
}

let threads = historyFromStorage();
const newThread = (cloud = false) => ({id: crypto.randomUUID(), mode: cloud ? 'kaggle' : 'local', messages: []});
let active = newThread();
let browsing = true;
let busy = false;
let ready = false;
let remote = null;
let categories = [];
let categoryView = null;
let categoryCountShown = pageSize;
let detailRequest = 0;

function remember() {
  if (!active.messages.length) return;
  active.updated = Date.now();
  threads = [active, ...threads.filter((item) => item.id !== active.id)].slice(0, 12);
  try {
    localStorage.setItem(storageKey, JSON.stringify(threads.map((item) => ({...item, messages: item.messages.slice(-40)}))));
  } catch {
    // The page remains usable when storage is disabled or full.
  }
}

function sourcesPanel(sources = [], highlighted = -1) {
  evidence.replaceChildren();
  if (!sources.length) {
    evidence.append(make('p', '有引用的回答会在这里显示来源，方便核对商品或条款。', 'empty-sources'));
    return;
  }
  sources.forEach((source, index) => {
    const item = make('details', '', 'source-item');
    item.open = index === highlighted;
    item.append(make('summary', `${index + 1} · ${source.label}`));
    if (source.excerpt) item.append(make('p', source.excerpt));
    if (source.member) item.append(make('code', `${source.member} · 第 ${source.line} 行`));
    const link = externalLink(source.url, '打开来源 ↗');
    if (link) item.append(link);
    evidence.append(item);
  });
}

function turn(content, assistant, pending = false) {
  const node = make('article', '', `turn ${assistant ? 'brand-turn' : 'user'}${pending ? ' loading' : ''}`);
  const body = make('div', '', 'turn-body');
  body.append(make('p', assistant ? '商品助手' : '你', 'turn-meta'), make('p', content.text, 'bubble'));
  if (content.products?.length) {
    const products = make('div', '', 'chat-products');
    products.setAttribute('aria-label', '相关商品图片');
    for (const product of content.products) {
      products.append(card(product.title, product.site, product.image, 'product-card', () => openProduct(product.id)));
    }
    body.append(products);
  }
  if (assistant && !pending) {
    const tools = make('div', '', 'turn-tools');
    content.sources?.forEach((source, index) => {
      tools.append(button(`${index + 1} · ${source.label}`, 'reference-button', () => {
        sourcesPanel(content.sources, index);
        evidence.closest('section').scrollIntoView({block: 'nearest'});
        evidence.querySelectorAll('summary')[index]?.focus({preventScroll: true});
      }));
    });
    tools.append(button('复制回答', 'quiet copy-answer', async (event) => {
      try {
        await navigator.clipboard.writeText(content.text);
        event.target.textContent = '已复制';
      } catch {
        showStatus('notice', '请选中回答正文后复制。');
      }
    }));
    body.append(tools);
  }
  node.append(body);
  return node;
}

function activeCloud() { return remote?.thread === active.id; }
function archived() { return active.mode === 'kaggle' && !activeCloud(); }

function renderChat() {
  const showConversation = !browsing && (active.messages.length > 0 || busy);
  byId('welcome').hidden = showConversation;
  conversation.hidden = !showConversation;
  byId('browse-button').textContent = showConversation || !active.messages.length ? '浏览类目 ↗' : '返回对话 ↗';
  conversation.replaceChildren(...active.messages.map((item) => turn(item, item.role === 'assistant')));
  if (busy && showConversation) conversation.append(turn({text: '正在查找资料…'}, true, true));
  if (showConversation) conversation.scrollTop = conversation.scrollHeight;
  sourcesPanel(active.messages.filter((item) => item.role === 'assistant').at(-1)?.sources);
  const mode = activeCloud() ? 'kaggle' : ready ? 'local' : 'offline';
  byId('mode').dataset.mode = mode;
  byId('mode-text').textContent = {kaggle: 'Kaggle GPU', local: '本机目录', offline: '尚未就绪'}[mode];
  byId('connection-button').textContent = remote ? 'Kaggle 连接 ↗' : '连接 Kaggle ↗';
  byId('disconnect').hidden = !remote;
  for (const id of ['new-chat', 'history-button', 'connection-button']) byId(id).disabled = busy;
  questionBox.disabled = busy || archived();
  byId('send').disabled = questionBox.disabled || !questionBox.value.trim();
}

async function send(text, visibleText = text) {
  if (busy || archived() || !text.trim()) return;
  const cloud = activeCloud();
  const user = {role: 'user', text: visibleText};
  active.messages.push(user);
  questionBox.value = '';
  browsing = false;
  busy = true;
  showStatus('notice', '');
  renderChat();
  try {
    const row = await post('/chat', {question: text, session_id: cloud ? remote.id : active.session || null});
    if (!cloud) active.session = row.action === 'exit' ? null : row.session_id;
    if (cloud && row.action === 'exit') remote = null;
    active.messages.push({role: 'assistant', text: row.answer, sources: row.sources, products: row.products});
    remember();
  } catch (error) {
    active.messages.pop();
    questionBox.value = text;
    if (error.status === 404 && !cloud) {
      active.session = null;
      if (active.messages.length) remember();
      showStatus('notice', '服务重启后这段对话的商品选择已过期。请重新指明商品，再发送问题。');
    } else if (cloud && (error.status === 404 || error.status === 502)) {
      remote = null;
      showStatus('notice', 'Kaggle 连接已中断。请重新连接，或新建本机对话。');
    } else {
      showStatus('notice', error.status ? error.message : '无法连接问答服务，请检查启动窗口后重试。');
    }
  } finally {
    busy = false;
    renderChat();
    if (!questionBox.disabled && matchMedia('(pointer: fine)').matches) questionBox.focus({preventScroll: true});
  }
}

function filteredCategories() {
  const phrase = filter.value.trim().toLowerCase().replaceAll(' ', '_');
  return categories.filter((category) => category.key.toLowerCase().includes(phrase) || category.label.toLowerCase().includes(phrase));
}

function showCategories(reset = true) {
  categoryView = null;
  byId('browse-title').textContent = '全部类目';
  byId('back-categories').hidden = true;
  filter.hidden = false;
  if (reset) {
    grid.replaceChildren();
    categoryCountShown = pageSize;
    scroller.scrollTop = 0;
  }
  const listed = filteredCategories();
  byId('browse-count').textContent = categories.length ? `${number.format(listed.length)} 个类目` : '';
  showStatus('browse-status', !categories.length ? '正在读取目录…' : listed.length ? '' : '没有匹配的类目。');
  grid.append(...listed.slice(grid.children.length, categoryCountShown).map((item) =>
    card(item.label, `${number.format(item.total)} 条`, item.image, 'category-card', () => openCategory(item))));
  byId('browse-more').textContent = '加载更多类目';
  byId('browse-more').disabled = false;
  byId('browse-more').hidden = grid.children.length >= listed.length;
}

async function moreProducts(view) {
  if (view.loading) return;
  view.loading = true;
  byId('browse-more').disabled = true;
  showStatus('browse-status', '正在加载商品…');
  try {
    const row = await request(`/catalog/categories/${encodeURIComponent(view.item.key)}/products?after=${view.after}`);
    if (categoryView !== view) return;
    grid.append(...row.items.map((item) => card(item.title, item.site, item.image, 'product-card', () => openProduct(item.id))));
    view.after = row.next_after;
    showStatus('browse-status', '');
    byId('browse-more').textContent = '加载更多商品';
    byId('browse-more').hidden = view.after === null;
  } catch {
    if (categoryView !== view) return;
    showStatus('browse-status', '这页商品暂未加载，请重试。');
    byId('browse-more').textContent = '重新加载商品';
    byId('browse-more').hidden = false;
  } finally {
    view.loading = false;
    if (categoryView === view) byId('browse-more').disabled = false;
  }
}

function openCategory(item) {
  categoryView = {item, after: 0, loading: false};
  byId('browse-title').textContent = item.label;
  byId('browse-count').textContent = `${number.format(item.total)} 条商品记录`;
  byId('back-categories').hidden = false;
  filter.hidden = true;
  grid.replaceChildren();
  scroller.scrollTop = 0;
  byId('browse-more').hidden = true;
  byId('back-categories').focus({preventScroll: true});
  moreProducts(categoryView);
}

async function openProduct(id) {
  const version = ++detailRequest;
  const dialog = byId('product-dialog');
  const content = byId('product-content');
  content.replaceChildren(make('p', '正在读取商品资料…'));
  if (!dialog.open) dialog.showModal();
  try {
    const item = await request(`/catalog/products/${id}`);
    if (version !== detailRequest || !dialog.open) return;
    const detail = make('div', '', 'product-detail');
    const left = make('div');
    left.append(picture(item.image, true));
    const original = externalLink(item.original_image, '查看 ABO 原图 ↗');
    if (original) left.append(original);
    const right = make('div');
    right.append(make('h3', item.title), make('p', `${item.category} · ${item.site}`));
    const fields = make('dl');
    for (const {label, value} of item.fields) {
      const entry = make('div');
      entry.append(make('dt', label), make('dd', value));
      fields.append(entry);
    }
    right.append(fields, make('p', `ABO 原始档案 ${item.archive} · 第 ${item.line} 行`, 'product-origin'));
    const ask = button('在对话中查看这款', 'primary', () => {
      dialog.close();
      send(`请查看 ${item.key} 的商品属性`, `请查看「${item.title}」的商品属性`);
    });
    ask.disabled = busy || archived();
    right.append(ask);
    detail.append(left, right);
    content.replaceChildren(detail);
  } catch (error) {
    if (version === detailRequest && dialog.open) content.replaceChildren(make('p', error.message));
  }
}

async function startThread() {
  if (busy) return;
  showStatus('notice', '');
  let warning = '';
  if (remote) {
    busy = true;
    renderChat();
    try {
      await post('/chat', {question: '重置', session_id: remote.id});
    } catch {
      remote = null;
      warning = 'Kaggle 会话已中断，已打开本机对话。';
    } finally {
      busy = false;
    }
  }
  active = newThread(Boolean(remote));
  if (remote) remote.thread = active.id;
  browsing = true;
  questionBox.value = '';
  filter.value = '';
  showCategories();
  showStatus('notice', warning);
  renderChat();
}

function showHistory() {
  const list = byId('history-list');
  list.replaceChildren();
  if (!threads.length) list.append(make('p', '开始提问后，历史对话会显示在这里。'));
  for (const thread of threads) {
    const option = button('', 'history-entry', () => {
      active = thread;
      browsing = false;
      questionBox.value = '';
      byId('history-dialog').close();
      showStatus('notice', archived() ? '这段 Kaggle 对话已归档。点击新对话继续，或重新连接 Kaggle。' : '');
      renderChat();
    });
    option.append(make('span', thread.messages[0]?.text?.slice(0, 85) || '商品问答'),
      make('small', `${thread.mode === 'kaggle' ? 'Kaggle GPU' : '本机目录'} · ${new Date(thread.updated).toLocaleString('zh-CN')}`));
    list.append(option);
  }
  byId('history-dialog').showModal();
}

function remoteDialog(busy, message = '') {
  byId('address').disabled = busy || Boolean(remote);
  byId('connect').hidden = Boolean(remote);
  byId('connect').disabled = busy;
  byId('disconnect').disabled = busy;
  showStatus('connection-status', message);
}

byId('connection-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const address = byId('address');
  const value = address.value.trim();
  if (!value || remote) return;
  remoteDialog(true, '正在连接 Kaggle…');
  address.value = '';
  try {
    const row = await post('/remote', {address: value});
    active = newThread(true);
    remote = {id: row.session_id, thread: active.id};
    browsing = true;
    questionBox.value = '';
    showStatus('notice', '');
    byId('connection-dialog').close();
    renderChat();
    remoteDialog(false, 'Kaggle GPU 已连接。');
  } catch (error) {
    remoteDialog(false, error.status ? error.message : '连接失败，请检查本机服务和 Kaggle 会话。');
    address.focus();
  }
});

byId('disconnect').addEventListener('click', async () => {
  if (!remote) return;
  remoteDialog(true, '正在断开连接…');
  try {
    await request(`/chat/${remote.id}`, {method: 'DELETE'});
    remote = null;
    if (active.mode === 'kaggle') active = newThread();
    browsing = !active.messages.length;
    questionBox.value = '';
    showStatus('notice', '');
    byId('connection-dialog').close();
    renderChat();
    remoteDialog(false);
  } catch (error) {
    remoteDialog(false, error.message);
  }
});

byId('connection-button').addEventListener('click', () => {
  remoteDialog(false, remote ? 'Kaggle GPU 已连接。' : '');
  byId('connection-dialog').showModal();
});
byId('history-button').addEventListener('click', showHistory);
byId('new-chat').addEventListener('click', startThread);
byId('browse-button').addEventListener('click', () => {
  browsing = !browsing || !active.messages.length;
  renderChat();
  (browsing ? byId('browser') : conversation).scrollIntoView({block: 'nearest'});
});
byId('back-categories').addEventListener('click', () => { showCategories(); filter.focus(); });
filter.addEventListener('input', () => showCategories());
byId('browse-more').addEventListener('click', () => {
  if (categoryView) moreProducts(categoryView);
  else { categoryCountShown += pageSize; showCategories(false); }
});
byId('composer').addEventListener('submit', (event) => { event.preventDefault(); send(questionBox.value.trim()); });
questionBox.addEventListener('input', () => { byId('send').disabled = !questionBox.value.trim(); });
questionBox.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault();
    byId('composer').requestSubmit();
  }
});
document.querySelectorAll('[data-close]').forEach((control) =>
  control.addEventListener('click', () => control.closest('dialog').close()));
document.querySelectorAll('[data-question]').forEach((control) =>
  control.addEventListener('click', () => send(control.dataset.question)));

async function initialize() {
  renderChat();
  try {
    const health = await request('/health');
    ready = health.status === 'ready';
    if (!ready) throw new Error('请先运行 scripts/start_web.py 准备商品目录。');
    const counts = health.catalog;
    for (const [id, key] of [['record-count', 'records'], ['category-count', 'categories'], ['market-count', 'marketplaces']]) {
      byId(id).textContent = number.format(counts[key]);
    }
    renderChat();
    categories = (await request('/catalog/categories')).items;
    showCategories();
  } catch (error) {
    showStatus('browse-status', error.status ? error.message : '目录暂未载入，请检查启动窗口后刷新页面。');
  }
}

initialize();
