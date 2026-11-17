/** @typedef {{id:number,code:string,equipment:string,parts:string[],note:string}} Job */
/** @typedef {{id:number,left_job_id:number,right_job_id:number,status:'active'|'revoked',created?:string,revoked?:string}} JobLink */
/** @typedef {{id:number,status:'draft'|'active'|'archived',groups:Array,link_ids:number[],created?:string,published?:string}} GroupVersion */
/** @typedef {{id:number,name:string,state:string,result:object,group_version_id:number|null,grouping:object}} MiningRun */
/** @typedef {{id:number,run_id:number,antecedent:string[],consequent:string[],support:number,confidence:number,lift:number}} AssociationRule */
/** @typedef {{id:number,name:string,rule_id:number,parts:string[],note:string,status:'draft'|'approved'|'suspended',evidence:object|null}} Kit */
