export function renderSummaryKeywords(container,settings){
 let input=container.querySelector('textarea');
 if(!input){
  container.innerHTML='<h2>自定义摘要排除关键词</h2><label for="custom-exclude-keywords">每行一个关键词，摘要包含其中任意一项就不参与进项比对</label><textarea id="custom-exclude-keywords" rows="5" placeholder="例如：网银手续费" aria-describedby="custom-keywords-help"></textarea><p id="custom-keywords-help" class="field-hint">按原文包含匹配，不支持通配符或正则表达式；xxx 没有特殊含义。首尾空白和重复项会自动整理，关键词内部空格保留。例如填写“网银手续费”会匹配“8月份网银手续费”；只填写“网银”也会排除“网银转账货款”。</p><p class="field-hint">此列表独立于上方内置规则开关，留空表示不添加自定义关键词。保存后立即应用于尚未建立有效金额关联的历史流水及后续新流水；已部分或全部关联的付款保留。移除关键词后，符合条件的流水会重新参与比对。</p>';
  input=container.querySelector('textarea');
 }
 input.value=(settings.custom_exclude_keywords||[]).join('\n');
}

export function readSummaryKeywords(container){
 return container.querySelector('textarea').value.split(/\r?\n/);
}
