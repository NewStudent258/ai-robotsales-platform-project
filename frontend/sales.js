const productGrid = document.querySelector('#sales-product-grid');
const quoteItems = document.querySelector('#quote-items');
const productDialog = document.querySelector('#product-dialog');
const quoteDialog = document.querySelector('#sales-quote-dialog');
const quoteForm = document.querySelector('#sales-quote-form');
const quoteError = document.querySelector('#sales-error');

let products = [];
let category = '全部';
let cart = new Map();
let currentQuote = null;
let orderKey = null;

const errorMessages = {
  PRODUCT_NOT_FOUND: '有产品已下架，请刷新目录后重新选择。',
  MIXED_CURRENCY: '报价单中存在不同币种的产品。',
  QUOTE_NOT_FOUND: '报价不存在，请重新生成。',
  QUOTE_VERSION_CONFLICT: '报价已更新，请重新生成。',
  QUOTE_EXPIRED: '报价已过期，请重新生成。',
  QUOTE_NOT_CONFIRMED: '报价尚未确认，请重试。',
  IDEMPOTENCY_KEY_REUSED: '订单提交信息已改变，请重新生成报价。',
  IDEMPOTENCY_RESULT_MISSING: '订单结果需要人工核查，请勿重复提交。',
};

function node(tag, className, value) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (value !== undefined) item.textContent = value;
  return item;
}

function button(label, className, action) {
  const item = node('button', className, label);
  item.type = 'button';
  item.addEventListener('click', action);
  return item;
}

function price(value, currency = 'CNY') {
  return new Intl.NumberFormat('zh-CN', { style: 'currency', currency }).format(Number(value));
}

async function request(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let code;
    try { code = (await response.json()).detail?.code; } catch { /* Use HTTP status below. */ }
    throw new Error(errorMessages[code] || `请求失败（${response.status}），请稍后重试。`);
  }
  return response.json();
}

function productVisual(product, className) {
  const visual = node('div', className);
  const shape = product.use_cases?.includes('仓储') ? 'carrier' : product.use_cases?.includes('服务') ? 'service' : product.use_cases?.includes('巡检') ? 'patrol' : product.capabilities?.includes('六轴控制') ? 'arm' : 'rover';
  visual.classList.add(`visual-${shape}`);
  visual.setAttribute('role', 'img');
  visual.setAttribute('aria-label', `${product.name}的造型示意图`);
  const robot = node('div', 'catalog-robot');
  robot.append(node('span', 'robot-top'), node('span', 'robot-core'), node('span', 'robot-sensor'), node('span', 'robot-base'), node('span', 'robot-wheel robot-wheel-left'), node('span', 'robot-wheel robot-wheel-right'));
  visual.append(node('div', 'visual-ground'), robot);
  return visual;
}

function productCategory(product) {
  return product.use_cases?.[0] || '机器人';
}

function renderProducts() {
  const search = document.querySelector('#product-search').value.trim().toLocaleLowerCase('zh-CN');
  const sort = document.querySelector('#product-sort').value;
  const visible = products.filter((product) => {
    const categoryMatch = category === '全部' || (product.use_cases || []).includes(category);
    const text = [product.name, product.description, ...(product.capabilities || []), ...(product.use_cases || [])].join(' ').toLocaleLowerCase('zh-CN');
    return categoryMatch && (!search || text.includes(search));
  });
  if (sort === 'price-asc') visible.sort((a, b) => Number(a.base_price) - Number(b.base_price));
  if (sort === 'price-desc') visible.sort((a, b) => Number(b.base_price) - Number(a.base_price));
  document.querySelector('#results-label').textContent = `找到 ${visible.length} 款产品`;
  productGrid.replaceChildren();
  if (!visible.length) {
    productGrid.append(node('div', 'catalog-loading', '没有匹配的产品，请尝试其他关键词或场景。'));
    return;
  }
  for (const product of visible) {
    const card = node('article', 'sales-product-card');
    card.append(productVisual(product, 'sales-product-visual'));
    const body = node('div', 'sales-product-body');
    const kicker = node('div', 'sales-product-kicker');
    kicker.append(node('span', '', product.sku), node('b', '', productCategory(product)));
    body.append(kicker, node('h3', '', product.name), node('p', '', product.description));
    const tags = node('div', 'sales-product-tags');
    for (const capability of (product.capabilities || []).slice(0, 3)) tags.append(node('span', '', capability));
    body.append(tags);
    const footer = node('div', 'sales-product-footer');
    const priceBox = node('div', 'sales-product-price');
    priceBox.append(node('small', '', '参考起价'), node('strong', '', price(product.base_price, product.currency)));
    const actions = node('div', 'sales-product-actions');
    actions.append(button('详情', 'detail-action', () => openProduct(product.id)), button('加入报价单 +', 'add-action', () => addToCart(product.id)));
    footer.append(priceBox, actions);
    body.append(footer);
    card.append(body);
    productGrid.append(card);
  }
}

function specLabel(key) {
  return ({ compute: '算力', camera: '摄像头', battery_hours: '续航', payload_kg: '载重', navigation: '导航', axes: '轴数', repeatability_mm: '重复精度', sensors: '传感器', screen_inches: '屏幕' })[key] || key;
}

function specValue(key, value) {
  return `${value}${({ battery_hours: ' 小时', payload_kg: ' kg', repeatability_mm: ' mm', screen_inches: ' 英寸' })[key] || ''}`;
}

function openProduct(id) {
  const product = products.find((item) => item.id === id);
  if (!product) return;
  const content = document.querySelector('#product-dialog-content');
  content.replaceChildren();
  content.append(node('h2', '', product.name), productVisual(product, 'product-dialog-visual'));
  content.append(node('p', 'product-dialog-copy', product.description));
  const specs = node('div', 'detail-specs');
  for (const [key, value] of Object.entries(product.specs || {})) {
    const row = node('div');
    row.append(node('span', '', specLabel(key)), node('strong', '', specValue(key, value)));
    specs.append(row);
  }
  content.append(specs);
  const actions = node('div', 'product-dialog-actions');
  actions.append(node('strong', '', price(product.base_price, product.currency)));
  actions.append(button('加入报价单 +', 'sales-button sales-button-green', () => { addToCart(product.id); productDialog.close(); }));
  content.append(actions);
  productDialog.showModal();
}

function persistCart() {
  try { localStorage.setItem('robotiq-sales-cart', JSON.stringify([...cart])); } catch { /* Cart remains usable in memory. */ }
}

function restoreCart() {
  try {
    const saved = JSON.parse(localStorage.getItem('robotiq-sales-cart') || '[]');
    if (Array.isArray(saved)) {
      cart = new Map(saved.filter(([id, quantity]) => products.some((item) => item.id === id) && Number.isInteger(quantity) && quantity > 0 && quantity <= 10000));
    }
  } catch { cart = new Map(); }
  persistCart();
}

function renderCart() {
  const quantity = [...cart.values()].reduce((sum, value) => sum + value, 0);
  const total = [...cart].reduce((sum, [id, count]) => {
    const product = products.find((item) => item.id === id);
    return sum + (product ? Number(product.base_price) * count : 0);
  }, 0);
  document.querySelector('#cart-count').textContent = String(quantity);
  document.querySelector('#cart-total').textContent = price(total);
  document.querySelector('#start-quote').disabled = cart.size === 0;
  quoteItems.replaceChildren();
  if (!cart.size) {
    const empty = node('div', 'empty-quote');
    empty.append(node('strong', '', '还没有选择产品'), node('span', '', '把产品加入报价单，服务端会按数量重新计算价格。'));
    quoteItems.append(empty);
    return;
  }
  for (const [id, count] of cart) {
    const product = products.find((item) => item.id === id);
    if (!product) continue;
    const item = node('div', 'quote-item');
    const info = node('div');
    info.append(node('h4', '', product.name), node('p', '', product.sku));
    const side = node('div', 'quote-item-side');
    side.append(node('strong', '', price(Number(product.base_price) * count, product.currency)));
    const stepper = node('div', 'quantity-stepper');
    const minus = button('−', '', () => changeQuantity(id, -1));
    minus.setAttribute('aria-label', `减少 ${product.name} 数量`);
    const plus = button('+', '', () => changeQuantity(id, 1));
    plus.setAttribute('aria-label', `增加 ${product.name} 数量`);
    stepper.append(minus, node('span', '', String(count)), plus);
    side.append(stepper);
    item.append(info, side);
    quoteItems.append(item);
  }
}

function addToCart(id) {
  if (!products.some((item) => item.id === id)) return;
  cart.set(id, Math.min((cart.get(id) || 0) + 1, 10000));
  persistCart();
  renderCart();
  document.querySelector('#quote-rail').classList.add('open');
}

function changeQuantity(id, change) {
  const next = (cart.get(id) || 0) + change;
  if (next <= 0) cart.delete(id);
  else cart.set(id, Math.min(next, 10000));
  persistCart();
  renderCart();
}

function showError(message) {
  quoteError.textContent = message;
  quoteError.hidden = !message;
}

function openQuote() {
  if (!cart.size) return;
  currentQuote = null;
  orderKey = null;
  quoteForm.reset();
  showError('');
  document.querySelector('#sales-quote-form-step').hidden = false;
  document.querySelector('#sales-quote-review-step').hidden = true;
  document.querySelector('#sales-quote-success').hidden = true;
  document.querySelector('#sales-quote-terms').checked = false;
  document.querySelector('#sales-confirm-order').disabled = true;
  document.querySelector('#sales-quote-summary').textContent = `${cart.size} 款产品 · ${[...cart.values()].reduce((a, b) => a + b, 0)} 件 · 服务端将重新核算正式报价`;
  document.querySelector('#quote-rail').classList.remove('open');
  quoteDialog.showModal();
}

quoteForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!quoteForm.reportValidity()) return;
  const submit = quoteForm.querySelector('button[type="submit"]');
  submit.disabled = true;
  showError('');
  const fields = new FormData(quoteForm);
  try {
    currentQuote = await request('/api/v1/quotes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        customer_name: fields.get('customer_name'),
        customer_email: fields.get('customer_email'),
        items: [...cart].map(([product_id, quantity]) => ({ product_id, quantity })),
      }),
    });
    orderKey = crypto.randomUUID();
    document.querySelector('#sales-quote-reference').textContent = `报价编号 ${currentQuote.quote_number}`;
    const lines = document.querySelector('#sales-quote-lines');
    lines.replaceChildren();
    for (const item of currentQuote.items) {
      const line = node('div', 'sales-quote-line');
      line.append(node('span', '', `${item.product_name} × ${item.quantity}`), node('strong', '', price(item.line_total, currentQuote.currency)));
      lines.append(line);
    }
    document.querySelector('#sales-quote-subtotal').textContent = price(currentQuote.subtotal, currentQuote.currency);
    document.querySelector('#sales-quote-tax').textContent = price(currentQuote.tax, currentQuote.currency);
    document.querySelector('#sales-quote-total').textContent = price(currentQuote.total, currentQuote.currency);
    document.querySelector('#sales-quote-expiry').textContent = `有效期至 ${new Date(currentQuote.expires_at).toLocaleDateString('zh-CN')}`;
    document.querySelector('#sales-quote-form-step').hidden = true;
    document.querySelector('#sales-quote-review-step').hidden = false;
  } catch (error) { showError(error.message); }
  finally { submit.disabled = false; }
});

document.querySelector('#sales-quote-terms').addEventListener('change', (event) => {
  document.querySelector('#sales-confirm-order').disabled = !event.target.checked;
});

document.querySelector('#sales-confirm-order').addEventListener('click', async (event) => {
  if (!currentQuote || !document.querySelector('#sales-quote-terms').checked) return;
  const submit = event.currentTarget;
  submit.disabled = true;
  showError('');
  try {
    await request(`/api/v1/quotes/${currentQuote.id}/confirm?version=${currentQuote.version}`, {
      method: 'POST', headers: { 'X-Quote-Token': currentQuote.access_token },
    });
  } catch (error) {
    if (error.message === errorMessages.QUOTE_VERSION_CONFLICT) {
      try {
        const latest = await request(`/api/v1/quotes/${currentQuote.id}`, {
          headers: { 'X-Quote-Token': currentQuote.access_token },
        });
        if (latest.status === 'CONFIRMED') currentQuote = latest;
        else throw error;
      } catch {
        showError(error.message);
        submit.disabled = false;
        return;
      }
    } else {
      showError(error.message);
      submit.disabled = false;
      return;
    }
  }
  try {
    const order = await request(`/api/v1/orders?quote_id=${currentQuote.id}`, {
      method: 'POST', headers: { 'Idempotency-Key': orderKey, 'X-Quote-Token': currentQuote.access_token },
    });
    document.querySelector('#sales-order-summary').textContent = `订单号 ${order.order_number} · 合计 ${price(order.total, currentQuote.currency)} · 已创建`;
    document.querySelector('#sales-quote-review-step').hidden = true;
    document.querySelector('#sales-quote-success').hidden = false;
    cart.clear();
    persistCart();
    renderCart();
  } catch (error) {
    showError(error.message);
    submit.disabled = false;
  }
});

document.querySelector('#product-search').addEventListener('input', renderProducts);
document.querySelector('#product-sort').addEventListener('change', renderProducts);
document.querySelectorAll('.category-tab').forEach((tab) => tab.addEventListener('click', () => {
  category = tab.dataset.category;
  document.querySelector('.category-tab.active').classList.remove('active');
  tab.classList.add('active');
  renderProducts();
}));
document.querySelector('#open-cart').addEventListener('click', () => document.querySelector('#quote-rail').classList.toggle('open'));
document.querySelector('#close-cart').addEventListener('click', () => document.querySelector('#quote-rail').classList.remove('open'));
document.querySelector('#start-quote').addEventListener('click', openQuote);
document.querySelector('#close-product').addEventListener('click', () => productDialog.close());
document.querySelector('#close-sales-quote').addEventListener('click', () => quoteDialog.close());
document.querySelector('#finish-sales-quote').addEventListener('click', () => quoteDialog.close());
for (const dialog of [productDialog, quoteDialog]) dialog.addEventListener('click', (event) => { if (event.target === dialog) dialog.close(); });

async function loadProducts() {
  try {
    products = (await request('/api/v1/products?page_size=100')).items;
    document.querySelector('#hero-product-total').textContent = String(products.length).padStart(2, '0');
    restoreCart();
    renderProducts();
    renderCart();
  } catch {
    productGrid.replaceChildren(node('div', 'catalog-loading', '产品目录暂时不可用，请稍后刷新页面。'));
    document.querySelector('#results-label').textContent = '产品目录暂不可用';
  }
}

loadProducts();
