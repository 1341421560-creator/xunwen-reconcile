export function paginate(rows,index,size){
 const last=Math.max(0,Math.ceil(rows.length/size)-1);
 index=Math.min(Math.max(index,0),last);
 return {rows:rows.slice(index*size,(index+1)*size),index,last,total:rows.length};
}

export function renderPager(kind,info,suffix=''){
 document.getElementById(`${kind}-page-info`).textContent=`共 ${info.total} 条 · 第 ${info.index+1} / ${info.last+1} 页${suffix}`;
 document.querySelector(`[data-pager="${kind}:-1"]`).disabled=info.index===0;
 document.querySelector(`[data-pager="${kind}:1"]`).disabled=info.index===info.last;
}
