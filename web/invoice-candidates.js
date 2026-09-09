export function invoiceCandidates(view,bank,includeOther=false){
 if(!bank)return [];
 return view.invoices.filter(invoice=>invoice.currency===bank.currency&&!invoice.hold_reasons.length&&invoice.status!=='review'&&invoice.distributable_cents>0&&(includeOther||bank.party_match_key&&invoice.party_match_key===bank.party_match_key));
}
