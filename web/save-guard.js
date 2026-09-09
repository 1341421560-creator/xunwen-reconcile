export function installSaveGuard(target,isSaving){
 target.addEventListener('beforeunload',event=>{
  if(!isSaving())return;
  event.preventDefault();event.returnValue='';
 });
}
