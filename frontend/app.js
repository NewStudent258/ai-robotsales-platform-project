const grid = document.querySelector('#product-grid');
const count = document.querySelector('#product-count');
const panel = document.querySelector('#assistant-panel');
const messages = document.querySelector('#chat-messages');
const input = document.querySelector('#chat-input');
let products = [];

function openAssistant() { panel.classList.add('open'); panel.setAttribute('aria-hidden', 'false'); setTimeout(() => input.focus(), 250); }
function closeAssistant() { panel.classList.remove('open'); panel.setAttribute('aria-hidden', 'true'); }
document.querySelectorAll('#open-assistant,#hero-assistant,#banner-assistant').forEach((button) => button.addEventListener('click', openAssistant));
document.querySelector('#close-assistant').addEventListener('click', closeAssistant);
document.querySelector('#close-assistant-button').addEventListener('click', closeAssistant);

function productMarkup(product) {
  const useCase = product.use_cases?.[0] || '机器人平台';
  return `<article class="product-card" data-use-cases="${(product.use_cases || []).join(',')}"><div class="product-visual"><div class="visual-block"></div></div><div class="product-card-body"><h3>${product.name}</h3><p>${product.description}</p><div class="card-meta"><span>${useCase} · ¥${Number(product.base_price).toLocaleString('zh-CN')}</span><span class="card-arrow">↗</span></div></div></article>`;
}
function renderProducts(filter = 'all') {
  const visible = filter === 'all' ? products : products.filter((product) => (product.use_cases || []).includes(filter));
  count.textContent = `${visible.length.toString().padStart(2, '0')} PRODUCTS`;
  grid.innerHTML = visible.length ? visible.map(productMarkup).join('') : '<div class="loading-state">暂时没有符合条件的产品。</div>';
}
async function loadProducts() {
  try { const response = await fetch('/api/v1/products?page_size=50'); if (!response.ok) throw new Error('products'); products = (await response.json()).items; renderProducts(); }
  catch { grid.innerHTML = '<div class="loading-state">产品目录暂时不可用，请稍后重试。</div>'; count.textContent = 'OFFLINE'; }
}
document.querySelectorAll('.filter').forEach((button) => button.addEventListener('click', () => { document.querySelector('.filter.active').classList.remove('active'); button.classList.add('active'); renderProducts(button.dataset.filter); }));

function addMessage(text, type) { const item = document.createElement('div'); item.className = `message ${type}-message`; item.innerHTML = type === 'assistant' ? `<span class="message-icon">R</span><div>${text}</div>` : `<div>${text}</div>`; messages.appendChild(item); messages.scrollTop = messages.scrollHeight; }
function addRecommendations(recommendations) { recommendations.forEach((recommendation) => { const item = document.createElement('div'); item.className = 'recommendation'; item.innerHTML = `<strong>${recommendation.name}</strong><small>${recommendation.reason}<br />参考价格：¥${Number(recommendation.base_price).toLocaleString('zh-CN')}</small>`; messages.appendChild(item); }); messages.scrollTop = messages.scrollHeight; }
async function sendMessage(text) { if (!text.trim()) return; addMessage(text, 'user'); input.value = ''; addMessage('正在查看产品能力和适配场景…', 'assistant'); const placeholder = messages.lastElementChild; try { const response = await fetch('/api/v1/assistant/messages', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({message: text}) }); const data = await response.json(); placeholder.remove(); addMessage(data.answer, 'assistant'); addRecommendations(data.recommendations || []); } catch { placeholder.remove(); addMessage('助手暂时无法连接，请稍后再试。', 'assistant'); } }
document.querySelector('#chat-form').addEventListener('submit', (event) => { event.preventDefault(); sendMessage(input.value); });
document.querySelectorAll('.quick-prompts button').forEach((button) => button.addEventListener('click', () => sendMessage(button.dataset.prompt)));
loadProducts();
