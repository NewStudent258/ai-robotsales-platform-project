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
    try { code = (await response.json()).detail?.code; } catch { /* Keep generic error. */ }
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
  } catch (error) {
    placeholder.remove();
    addMessage(error.message, 'assistant');
  }
}
document.querySelector('#chat-form').addEventListener('submit', (event) => { event.preventDefault(); sendMessage(chatInput.value); });
document.querySelectorAll('.quick-prompts button').forEach((button) => button.addEventListener('click', () => sendMessage(button.dataset.prompt)));

quoteForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!selectedProduct || !quoteForm.reportValidity()) return;
  const submit = quoteForm.querySelector('button[type="submit"]');
  submit.disabled = true;
  showQuoteError('');
  const fields = new FormData(quoteForm);
  try {
    currentQuote = await request('/api/v1/quotes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        customer_name: fields.get('customer_name'),
        customer_email: fields.get('customer_email'),
        items: [{ product_id: selectedProduct.id, quantity: Number(fields.get('quantity')) }],
      }),
    });
    orderKey = crypto.randomUUID();
    document.querySelector('#quote-ref').textContent = `报价编号 ${currentQuote.quote_number}`;
    const lines = document.querySelector('#quote-lines');
    lines.replaceChildren();
    for (const item of currentQuote.items) {
      lines.append(element('div', 'quote-line', `${item.product_name} × ${item.quantity} · ${currency(item.line_total, currentQuote.currency)}`));
    }
    document.querySelector('#quote-subtotal').textContent = currency(currentQuote.subtotal, currentQuote.currency);
    document.querySelector('#quote-tax').textContent = currency(currentQuote.tax, currentQuote.currency);
    document.querySelector('#quote-total').textContent = currency(currentQuote.total, currentQuote.currency);
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
  currentQuote = null;
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
