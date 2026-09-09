export async function request(path, payload) {
  const options = payload === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) };
  let response,data;
  try {
    response = await fetch(path, options);
    data = await response.json();
  } catch {
    throw new Error(payload === undefined ? '暂时无法读取本机服务，请确认软件已启动后刷新账本。' : '连接中断或响应不完整，提交结果尚未确认。请刷新账本检查是否已保存，再决定是否重试。');
  }
  if (!response.ok) {
    const error = new Error(data.error || '操作失败，请重试');
    error.status = response.status;
    throw error;
  }
  return data;
}

export function readUpload(file) {
  if (!file || !/\.xlsx?$/i.test(file.name)) throw new Error('请选择 .xls 或 .xlsx 文件');
  if (!file.size || file.size > 20 * 1024 * 1024) throw new Error('每个文件必须大于 0 且不超过 20 MB');
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({ name: file.name, content: String(reader.result).split(',')[1] });
    reader.onerror = () => reject(new Error('文件读取失败，请重新选择'));
    reader.readAsDataURL(file);
  });
}
