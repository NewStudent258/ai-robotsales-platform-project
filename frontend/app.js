const grid = document.querySelector('#product-grid');
const count = document.querySelector('#product-count');
const assistantPanel = document.querySelector('#assistant-panel');
const messages = document.querySelector('#chat-messages');
const chatInput = document.querySelector('#chat-input');
const quoteDialog = document.querySelector('#quote-dialog');
const quoteForm = document.querySelector('#quote-form');
const quoteError = document.querySelector('#quote-error');

let products = [];
let selectedProduct = null;
let currentQuote = null;
let orderKey = null;
let sessionId = null;

const errorMessages = {
  PRODUCT_NOT_FOUND: '产品已下架，请重新选择。',
  MIXED_CURRENCY: '所选产品币种不同，无法合并报价。',
  QUOTE_NOT_FOUND: '报价不存在，请重新生成。',
  QUOTE_VERSION_CONFLICT: '报价已更新，请重新生成。',
  QUOTE_EXPIRED: '报价已过期，请重新生成。',
  QUOTE_NOT_CONFIRMED: '报价尚未确认，请重试。',
  IDEMPOTENCY_KEY_REUSED: '订单提交信息已改变，请重新生成报价。',
  IDEMPOTENCY_RESULT_MISSING: '订单结果需要人工核查，请勿重复提交。',
};

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function request(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let code;
    try {
      const body = await response.json();
      // 统一错误封套 {error:{code}}；兼容旧的 {detail:{code}}。
      code = body.error?.code || body.detail?.code;
    } catch { /* Keep generic error. */ }
    throw new Error(errorMessages[code] || `请求失败（${response.status}），请稍后重试。`);
  }
  return response.json();
}

function currency(amount, code = 'CNY') {
  return new Intl.NumberFormat('zh-CN', { style: 'currency', currency: code }).format(Number(amount));
}

function openAssistant() {
  assistantPanel.classList.add('open');
  assistantPanel.setAttribute('aria-hidden', 'false');
  chatInput.focus();
}
function closeAssistant() {
  assistantPanel.classList.remove('open');
  assistantPanel.setAttribute('aria-hidden', 'true');
}
document.querySelectorAll('#open-assistant,#hero-assistant,#banner-assistant').forEach((button) => button.addEventListener('click', openAssistant));
document.querySelector('#close-assistant').addEventListener('click', closeAssistant);
document.querySelector('#close-assistant-button').addEventListener('click', closeAssistant);
document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeAssistant(); });

function showQuoteError(message) {
  quoteError.textContent = message;
  quoteError.hidden = !message;
}
function openQuote(productId) {
  selectedProduct = products.find((product) => product.id === productId);
  if (!selectedProduct) return;
  closeAssistant();
  currentQuote = null;
  orderKey = null;
  quoteForm.reset();
  document.querySelector('#selected-product').textContent = selectedProduct.name;
  document.querySelector('#quote-step-form').hidden = false;
  document.querySelector('#quote-step-review').hidden = true;
  document.querySelector('#quote-step-success').hidden = true;
  document.querySelector('#quote-terms').checked = false;
  document.querySelector('#confirm-order').disabled = true;
  showQuoteError('');
  quoteDialog.showModal();
}
document.querySelector('#close-quote').addEventListener('click', () => quoteDialog.close());
document.querySelector('#finish-order').addEventListener('click', () => quoteDialog.close());
quoteDialog.addEventListener('click', (event) => { if (event.target === quoteDialog) quoteDialog.close(); });

function renderProducts(filter = 'all') {
  const visible = filter === 'all' ? products : products.filter((product) => (product.use_cases || []).includes(filter));
  count.textContent = `${String(visible.length).padStart(2, '0')} PRODUCTS`;
  grid.replaceChildren();
  if (!visible.length) {
    grid.append(element('div', 'loading-state', '暂时没有符合条件的产品。'));
    return;
  }
  for (const product of visible) {
    const card = element('article', 'product-card');
    const visual = element('div', 'product-visual');
    if (product.image_url && /^(https?:\/\/|\/static\/)/.test(product.image_url)) {
      const image = element('img', 'product-image');
      image.src = product.image_url;
      image.alt = product.name;
      visual.append(image);
    } else {
      visual.append(element('div', 'visual-block'));
    }
    const body = element('div', 'product-card-body');
    body.append(element('h3', '', product.name));
    body.append(element('p', '', product.description));
    body.append(element('div', 'card-meta', `${product.use_cases?.[0] || '机器人平台'} · ${currency(product.base_price, product.currency)}`));
    const actions = element('div', 'card-actions');
    const quoteButton = element('button', 'card-primary', '获取报价 →');
    quoteButton.type = 'button';
    quoteButton.addEventListener('click', () => openQuote(product.id));
    const askButton = element('button', 'card-secondary', '咨询助手');
    askButton.type = 'button';
    askButton.addEventListener('click', () => { openAssistant(); chatInput.value = `这款${product.name}适合什么场景？`; });
    actions.append(quoteButton, askButton);
    body.append(actions);
    card.append(visual, body);
    grid.append(card);
  }
}

async function loadProducts() {
  try {
    products = (await request('/api/v1/products?page_size=50')).items;
    renderProducts();
  } catch {
    grid.replaceChildren(element('div', 'loading-state', '产品目录暂时不可用，请稍后重试。'));
    count.textContent = 'OFFLINE';
  }
}
document.querySelectorAll('.filter').forEach((button) => button.addEventListener('click', () => {
  document.querySelector('.filter.active').classList.remove('active');
  button.classList.add('active');
  renderProducts(button.dataset.filter);
}));

function addMessage(text, type) {
  const item = element('div', `message ${type}-message`);
  if (type === 'assistant') item.append(element('span', 'message-icon', 'R'));
  item.append(element('div', '', text));
  messages.append(item);
  messages.scrollTop = messages.scrollHeight;
  return item;
}
function addRecommendations(recommendations) {
  for (const recommendation of recommendations) {
    const item = element('div', 'recommendation');
    item.append(element('strong', '', recommendation.name));
    item.append(element('small', '', recommendation.reason));
    item.append(element('small', '', `参考起价：${currency(recommendation.base_price)}`));
    const button = element('button', 'recommendation-action', '配置并报价 →');
    button.type = 'button';
    button.addEventListener('click', () => openQuote(recommendation.product_id));
    item.append(button);
    messages.append(item);
  }
  messages.scrollTop = messages.scrollHeight;
}

// 助手已生成正式报价：直接进入报价核对步骤，把 Agent 与交易链路接起来。
function addAssistantQuote(quote, pendingAction) {
  const item = element('div', 'recommendation assistant-quote');
  item.append(element('strong', '', `报价 ${quote.quote_number}`));
  item.append(element('small', '', `合计 ${currency(quote.total, quote.currency)}`));
  if (pendingAction?.requires_confirmation) {
    item.append(element('small', '', pendingAction.message));
  }
  const button = element('button', 'recommendation-action', '查看并确认报价 →');
  button.type = 'button';
  button.addEventListener('click', () => reviewAssistantQuote(quote));
  item.append(button);
  messages.append(item);
  messages.scrollTop = messages.scrollHeight;
}

// 统一的金额构成渲染：折扣、税额、生效规则与版本链。
// 两条报价路径共用，避免展示口径漂移。
function renderQuoteTotals(quote) {
  const code = quote.currency;
  document.querySelector('#quote-subtotal').textContent = currency(quote.subtotal, code);

  const discountRow = document.querySelector('#quote-discount-row');
  const discount = Number(quote.discount || 0);
  discountRow.hidden = !(discount > 0);
  if (discount > 0) {
    document.querySelector('#quote-discount').textContent = `−${currency(discount, code)}`;
  }

  document.querySelector('#quote-tax').textContent = currency(quote.tax || 0, code);
  document.querySelector('#quote-total').textContent = currency(quote.total, code);

  // 生效规则：让客户看到优惠/税额的依据，而不是只给一个数字。
  const rules = document.querySelector('#quote-rules');
  const applied = quote.applied_rules || [];
  rules.replaceChildren();
  rules.hidden = !applied.length;
  for (const rule of applied) {
    rules.append(element('li', '', `${rule.detail || rule.name}（规则 ${rule.code} v${rule.version}）`));
  }

  // 版本链：重新报价后客户可以看到历史版本与被取代状态。
  const versionNode = document.querySelector('#quote-versions');
  const versions = quote.versions || [];
  versionNode.hidden = versions.length <= 1;
  versionNode.textContent = versions.length > 1
    ? `版本记录：${versions.map((item) => `v${item.version} ${currency(item.total, code)}${item.is_current ? '（当前）' : ''}`).join(' · ')}`
    : '';
}

// 用已有报价打开确认窗口，跳过重复填表，但仍需客户勾选条款。
async function reviewAssistantQuote(quote) {
  closeAssistant();
  selectedProduct = null;
  currentQuote = {
    id: quote.quote_id,
    quote_number: quote.quote_number,
    currency: quote.currency,
    total: quote.total,
    subtotal: quote.subtotal ?? quote.total,
    discount: quote.discount || 0,
    tax: quote.tax || 0,
    version: quote.version,
    expires_at: quote.expires_at,
    items: [],
    applied_rules: [],
    versions: [],
    access_token: null,
    session_id: sessionId,
  };
  orderKey = crypto.randomUUID();
  quoteForm.reset();
  document.querySelector('#quote-ref').textContent = `报价编号 ${quote.quote_number}`;
  const lines = document.querySelector('#quote-lines');
  lines.replaceChildren(element('div', 'quote-line', '该报价由智能助手生成，请在确认前核对金额与有效期。'));
  renderQuoteTotals(currentQuote);
  document.querySelector('#quote-expiry').textContent = quote.expires_at
    ? `有效期至 ${new Date(quote.expires_at).toLocaleDateString('zh-CN')}`
    : '';
  document.querySelector('#quote-step-form').hidden = true;
  document.querySelector('#quote-step-review').hidden = false;
  document.querySelector('#quote-step-success').hidden = true;
  document.querySelector('#quote-terms').checked = false;
  document.querySelector('#confirm-order').disabled = true;
  showQuoteError('');
  quoteDialog.showModal();
}

// 令牌在用户真正要确认时才按需签发，不随对话响应回流。
async function ensureQuoteToken() {
  if (!currentQuote) return null;
  if (currentQuote.access_token) return currentQuote.access_token;
  const data = await request('/api/v1/assistant/quote-token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: currentQuote.session_id, quote_id: currentQuote.id }),
  });
  currentQuote.access_token = data.access_token;
  return currentQuote.access_token;
}

async function sendMessage(text) {
  if (!text.trim()) return;
  addMessage(text, 'user');
  chatInput.value = '';
  const placeholder = addMessage('正在查看产品能力和适配场景…', 'assistant');
  try {
    const data = await request('/api/v1/assistant/messages', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, session_id: sessionId }),
    });
    sessionId = data.session_id;
    placeholder.remove();
    addMessage(data.answer, 'assistant');
    addRecommendations(data.recommendations || []);
    if (data.quote) addAssistantQuote(data.quote, data.pending_action);
    if (data.handoff_required) {
      addMessage('该请求已标记为需要人工跟进，我们的销售同事会尽快联系你。', 'assistant');
    }
  } catch (error) {
    placeholder.remove();
    addMessage(error.message, 'assistant');
  }
}
document.querySelector('#chat-form').addEventListener('submit', (event) => { event.preventDefault(); sendMessage(chatInput.value); });
document.querySelectorAll('.quick-prompts button').forEach((button) => button.addEventListener('click', () => sendMessage(button.dataset.prompt)));

quoteForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!quoteForm.reportValidity()) return;
  // 助手生成的报价没有 selectedProduct，此时沿用该报价首行产品。
  const productId = selectedProduct?.id ?? currentQuote?.items?.[0]?.product_id;
  if (!productId) return;
  const submit = quoteForm.querySelector('button[type="submit"]');
  submit.disabled = true;
  showQuoteError('');
  const fields = new FormData(quoteForm);
  try {
    const body = {
      customer_name: fields.get('customer_name'),
      customer_email: fields.get('customer_email'),
      items: [{ product_id: productId, quantity: Number(fields.get('quantity')) }],
    };
    if (currentQuote && currentQuote.id) {
      // 已有报价：生成新版本，旧版由服务端标记为被取代，历史得以保留。
      const token = await ensureQuoteToken();
      const revised = await request(`/api/v1/quotes/${currentQuote.id}/revise`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Quote-Token': token },
        body: JSON.stringify({ ...body, expected_version: currentQuote.version }),
      });
      // 新版本自带新令牌；旧令牌随旧版本失效，必须一并替换。
      revised.access_token = revised.access_token || token;
      revised.session_id = currentQuote.session_id;
      currentQuote = revised;
    } else {
      currentQuote = await request('/api/v1/quotes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
    }
    orderKey = crypto.randomUUID();
    document.querySelector('#quote-ref').textContent = `报价编号 ${currentQuote.quote_number}`;
    const lines = document.querySelector('#quote-lines');
    lines.replaceChildren();
    for (const item of currentQuote.items) {
      lines.append(element('div', 'quote-line', `${item.product_name} × ${item.quantity} · ${currency(item.line_total, currentQuote.currency)}`));
    }
    renderQuoteTotals(currentQuote);
    document.querySelector('#quote-expiry').textContent = `有效期至 ${new Date(currentQuote.expires_at).toLocaleDateString('zh-CN')}`;
    document.querySelector('#quote-step-form').hidden = true;
    document.querySelector('#quote-step-review').hidden = false;
  } catch (error) {
    showQuoteError(error.message);
  } finally {
    submit.disabled = false;
  }
});

document.querySelector('#quote-terms').addEventListener('change', (event) => {
  document.querySelector('#confirm-order').disabled = !event.target.checked;
});
document.querySelector('#edit-configuration').addEventListener('click', () => {
  // 保留 currentQuote：再次提交时以它为基础生成新版本，而不是丢掉历史。
  orderKey = null;
  document.querySelector('#quote-terms').checked = false;
  document.querySelector('#confirm-order').disabled = true;
  document.querySelector('#quote-step-review').hidden = true;
  document.querySelector('#quote-step-form').hidden = false;
  showQuoteError('');
});
document.querySelector('#confirm-order').addEventListener('click', async (event) => {
  if (!currentQuote || !document.querySelector('#quote-terms').checked) return;
  const button = event.currentTarget;
  button.disabled = true;
  showQuoteError('');
  try {
    // 助手生成的报价在此刻才换取访问令牌。
    await ensureQuoteToken();
  } catch (error) {
    showQuoteError(error.message);
    button.disabled = false;
    return;
  }
  try {
    await request(`/api/v1/quotes/${currentQuote.id}/confirm?version=${currentQuote.version}`, {
      method: 'POST',
      headers: { 'X-Quote-Token': currentQuote.access_token },
    });
  } catch (error) {
    if (error.message !== errorMessages.QUOTE_VERSION_CONFLICT) {
      showQuoteError(error.message);
      button.disabled = false;
      return;
    }
  }
  try {
    const order = await request(`/api/v1/orders?quote_id=${currentQuote.id}`, {
      method: 'POST',
      headers: { 'Idempotency-Key': orderKey, 'X-Quote-Token': currentQuote.access_token },
    });
    document.querySelector('#order-summary').textContent = `订单号 ${order.order_number} · 合计 ${currency(order.total, currentQuote.currency)} · 已创建`;
    document.querySelector('#quote-step-review').hidden = true;
    document.querySelector('#quote-step-success').hidden = false;
  } catch (error) {
    showQuoteError(error.message);
    button.disabled = false;
  }
});

loadProducts();
if (new URLSearchParams(window.location.search).get('assistant') === '1') openAssistant();
