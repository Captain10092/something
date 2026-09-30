// Presentation only: every number comes from /api/summary (services.py).
const $ = s => document.querySelector(s);
const inr = n => (n < 0 ? '-' : '') + '₹' + Math.abs(Math.round(n)).toLocaleString('en-IN');
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const today = () => new Date().toLocaleDateString('en-CA');
const GUESS = {tea:'Food',coffee:'Food',lunch:'Food',dinner:'Food',snack:'Food',netflix:'Entertainment',movie:'Entertainment',
  bgmi:'Gaming',uc:'Gaming',bus:'Transport',metro:'Transport',auto:'Transport',uber:'Transport',rent:'Rent',sip:'SIP / Investment',vegetables:'Groceries'};
let month = null, editId = null, cats = [];

async function api(url, method = 'GET', body) {
  const r = await fetch(url, {method, headers: {'Content-Type': 'application/json'}, body: body && JSON.stringify(body)});
  const d = await r.json();
  if (!r.ok) throw d;
  return d;
}
const monthLabel = m => new Date(m + '-01T00:00').toLocaleDateString('en-IN', {month: 'long', year: 'numeric'});

async function refresh() {
  const p = new URLSearchParams({month, category: $('#fc').value, q: $('#q').value, date: $('#fd').value});
  const [months, s, rows] = await Promise.all([api('/api/months'), api('/api/summary?month=' + month), api('/api/expenses?' + p)]);
  if (!months.includes(month)) months.unshift(month);
  $('#month').innerHTML = months.map(m => `<option value="${m}" ${m === month ? 'selected' : ''}>${monthLabel(m)}</option>`).join('');
  renderSummary(s); renderRows(rows);
}

function renderSummary(s) {
  if (!cats.length) { cats = s.categories.map(c => c.name); fillSelects(); }
  $('#c-budget').textContent = inr(s.monthly_budget);
  $('#c-used').textContent = inr(s.used);
  $('#c-pct').textContent = s.pct_used + '% used';
  $('#c-rem').textContent = inr(s.remaining);
  $('#card-rem').classList.toggle('neg', s.remaining < 0);
  const bar = $('#c-bar'); bar.style.width = Math.min(s.pct_used, 100) + '%';
  bar.className = s.remaining < 0 ? 'over' : s.pct_used >= 80 ? 'warn' : '';
  $('#wealth').innerHTML = `Spending: ${inr(s.spending)} · Invested this month: ${inr(s.invested)} · Saved this month: ${inr(s.saved)} · Total wealth allocation: ${inr(s.wealth)}`;
  const a = [];
  if (s.over_by) a.push(`<span class="over">⚠ Total over budget by ${inr(s.over_by)}</span>`);
  s.categories.filter(c => c.status === 'over').forEach(c => a.push(`<span class="over">⚠ ${esc(c.name)}: over budget by ${inr(c.over_by)}</span>`));
  s.categories.filter(c => c.status === 'warn').forEach(c => a.push(`<span class="warn">● ${esc(c.name)} at ${c.pct}% of budget</span>`));
  if (!s.budgets_match) a.push(`<span class="warn">● Category budgets total ${inr(s.budgets_total)} — ${inr(Math.abs(s.budgets_diff))} ${s.budgets_diff > 0 ? 'above' : 'below'} ${inr(s.monthly_budget)}</span>`);
  $('#alerts').innerHTML = a.join('<br>');
  $('#cats').innerHTML = s.categories.map(c => `<div class="cat">
    <h3>${esc(c.name)} ${c.group !== 'spend' ? `<span class="tag">${c.group === 'invest' ? 'invested' : 'saved'}</span>` : ''}</h3>
    <div class="bar"><i class="${c.status}" style="width:${Math.min(c.pct, 100)}%"></i></div>
    <dl><dt>Budget</dt><dd>${inr(c.budget)}</dd><dt>Spent</dt><dd>${inr(c.spent)}</dd>
    <dt>Remaining</dt><dd class="${c.status === 'over' ? 'over' : ''}">${c.status === 'over' ? 'Over by ' + inr(c.over_by) : inr(c.remaining)}</dd>
    <dt>Used</dt><dd>${c.pct}%</dd></dl></div>`).join('');
  $('#budget-form').innerHTML = s.categories.map(c => `<label>${esc(c.name)}<input data-cat="${esc(c.name)}" inputmode="numeric" value="${c.budget}"></label>`).join('');
  $('#budget-total').innerHTML = `Total: <b>${inr(s.budgets_total)}</b> of ${inr(s.monthly_budget)}` + (s.budgets_match ? '' : ' <span class="warn">— does not match</span>');
}

function renderRows(rows) {
  window._rows = rows;
  $('#rows').innerHTML = rows.length ? rows.map(e => `<tr><td>${e.date}</td><td>${esc(e.description)}${e.notes ? `<br><small class="muted">${esc(e.notes)}</small>` : ''}</td>
    <td>${esc(e.category)}</td><td class="r">${inr(e.amount)}</td>
    <td class="r"><button class="ghost" onclick="startEdit(${e.id})">Edit</button><button class="del" onclick="del(${e.id})">Delete</button></td></tr>`).join('')
    : '<tr><td colspan="5" class="muted">No transactions</td></tr>';
}

function fillSelects() {
  $('#f-category').innerHTML = '<option value="">Choose…</option>' + cats.map(c => `<option>${esc(c)}</option>`).join('');
  $('#fc').innerHTML = '<option value="">All categories</option>' + cats.map(c => `<option>${esc(c)}</option>`).join('');
}

function resetForm() {
  editId = null;
  ['amount', 'description', 'notes'].forEach(k => $('#f-' + k).value = '');
  $('#f-category').value = ''; $('#f-date').value = today(); $('#quick').value = '';
  $('#form-title').textContent = 'Add expense'; $('#save').textContent = 'Add expense';
  $('#cancel').hidden = true; $('#form-err').textContent = '';
}

window.startEdit = id => {
  const e = window._rows.find(r => r.id === id);
  $('#f-amount').value = e.amount; $('#f-category').value = e.category; $('#f-description').value = e.description;
  $('#f-date').value = e.date; $('#f-notes').value = e.notes; editId = id;
  $('#form-title').textContent = 'Edit expense'; $('#save').textContent = 'Update expense'; $('#cancel').hidden = false;
  scrollTo({top: 0, behavior: 'smooth'});
};
window.del = async id => { if (confirm('Delete this expense?')) { await api('/api/expenses/' + id, 'DELETE'); refresh(); } };

async function save() {
  const body = {amount: $('#f-amount').value, category: $('#f-category').value, description: $('#f-description').value,
    date: $('#f-date').value, notes: $('#f-notes').value};
  try {
    await api(editId ? '/api/expenses/' + editId : '/api/expenses', editId ? 'PUT' : 'POST', body);
    month = body.date.slice(0, 7);
    resetForm(); refresh();
  } catch (e) { $('#form-err').textContent = Object.values(e.errors || {error: 'Failed'}).join(' · '); }
}

// "Tea 20" / "Amazon ₹577" -> fills the form; category guessed only for known words.
$('#quick').addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  const m = e.target.value.trim().match(/^(.*?)\s*₹?\s*([\d,]+)$/);
  if (!m) { $('#form-err').textContent = 'Type a description then an amount, e.g. Tea 20'; return; }
  $('#f-description').value = m[1]; $('#f-amount').value = m[2].replace(/,/g, '');
  const g = GUESS[m[1].toLowerCase().split(/\s+/).find(w => GUESS[w]) || ''];
  if (g) { $('#f-category').value = g; save(); } else { $('#f-category').focus(); $('#form-err').textContent = 'Choose a category, then Add'; }
});

$('#save').onclick = save;
$('#cancel').onclick = resetForm;
$('#month').onchange = e => { month = e.target.value; refresh(); };
['#q', '#fc', '#fd'].forEach(s => $(s).addEventListener('input', refresh));
$('#clear').onclick = () => { $('#q').value = $('#fc').value = $('#fd').value = ''; refresh(); };
$('#save-budgets').onclick = async () => {
  const b = {}; document.querySelectorAll('[data-cat]').forEach(i => b[i.dataset.cat] = i.value);
  try { renderSummary(await api('/api/budgets/' + month, 'PUT', b)); } catch (e) { alert(Object.values(e.errors).join('\n')); }
};

month = new Date().toLocaleDateString('en-CA').slice(0, 7);
resetForm(); refresh();
