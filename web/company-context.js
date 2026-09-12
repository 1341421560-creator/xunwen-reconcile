export class StaleCompanyResponse extends Error {
 constructor(){super('公司已切换，已忽略原公司的响应');this.silent=true;}
}

export function createCompanyContext(transport,{storage,onBusy=()=>{}}={}){
 let key='moderate',generation=0,busy=0;
 try{key=storage?.getItem('xunwen-company')||key;}catch{}
 const reads=new Set(['/api/ledger','/api/restore','/api/expense-statistics','/api/invoice-offset/preview']);
 function current(captured){return captured.key===key&&captured.generation===generation;}
 function capture(){
  const scope={key,generation};
  scope.assert=()=>{if(!current(scope))throw new StaleCompanyResponse();};
  scope.hold=()=>{scope.assert();busy++;onBusy(busy>0);let released=false;return()=>{if(!released){released=true;busy--;onBusy(busy>0);}};};
  scope.request=async(path,payload)=>{
   scope.assert();const write=payload!==undefined&&!reads.has(path);const release=write?scope.hold():()=>{};
   try{
    const url=payload===undefined?path+(path.includes('?')?'&':'?')+'company_key='+encodeURIComponent(scope.key):path;
    const result=await transport(url,payload===undefined?undefined:{...payload,company_key:scope.key});
    scope.assert();if(result.company_key!==scope.key)throw new Error('响应公司与当前请求不一致，请刷新账本');
    return result;
   }catch(error){scope.assert();throw error;}finally{release();}
  };
  return scope;
 }
 return {get key(){return key;},get busy(){return busy>0;},capture,
  request:(path,payload)=>capture().request(path,payload),
  select(next){if(busy)throw new Error('正在保存，请等待完成后切换公司');key=next;generation++;try{storage?.setItem('xunwen-company',key);}catch{}}
 };
}
