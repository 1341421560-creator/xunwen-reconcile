export function filterExpenseStatistics(result,{category='all',party=''}={}){
 const query=party.trim().toLocaleLowerCase();
 const rows=result.rows.filter(row=>(category==='all'||row.expense_category===category)&&(!query||(row.party||'').toLocaleLowerCase().includes(query)));
 const categories=result.categories.map(item=>({...item,count:0,debit_cents:0}));
 const totals=new Map(categories.map(item=>[item.id,item]));
 let debit_cents=0;
 for(const row of rows){const item=totals.get(row.expense_category);item.count++;item.debit_cents+=row.debit_cents;debit_cents+=row.debit_cents;}
 return {...result,rows,categories,count:rows.length,debit_cents};
}

export function expenseCompanyOptions(rows){
 return [...new Set(rows.map(row=>row.party).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'zh-CN'));
}
