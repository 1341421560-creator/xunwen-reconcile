export const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const money = value => (value / 100).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
export const badge = (status, labels) => `<span class="badge ${escape(status)}">${escape(labels[status])}</span>`;
export const location = row => `${escape(row.sheet)} · 第 ${row.row} 行`;
export const el = id => document.getElementById(id);
