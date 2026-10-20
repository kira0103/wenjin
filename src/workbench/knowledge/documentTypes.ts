export type LibraryScope={category_id?:string;uncategorized?:boolean;include_core?:boolean;library_kind?:'all'|'ancient'|'papers';project_id?:string|null;document_group_id?:string;document_uncategorized?:boolean;document_path?:string;document_entry_ids?:string[];asset_ids?:string[]};
export type DirectoryLocation={group:string;path:string};
type DocumentGroup={id:string;name:string;files:number;version:number};
export type DocumentGroups={revision:number;total:number;uncategorized:number;items:DocumentGroup[]};
export type DocumentEntry={id:string;group_id:string|null;group_name?:string|null;edition_id:string;asset_id:string|null;relative_path:string;title:string;item_type:string;active_revision:string|null;index_state:string|null;is_core:boolean;size_bytes:number|null;version:number};
export type DirectoryResult={directories:{name:string;path:string;files:number}[];files:DocumentEntry[];path:string;total:number;next_offset:number|null};
export function directoryScope(location:DirectoryLocation):LibraryScope{return {include_core:true,...(location.group==='uncategorized'?{document_uncategorized:true}:location.group?{document_group_id:location.group}:{}),...(location.path?{document_path:location.path}:{})};}
export function directoryParams(location:DirectoryLocation):URLSearchParams{return new URLSearchParams({...(location.group==='uncategorized'?{uncategorized:'true'}:location.group?{group_id:location.group}:{}),path:location.path});}
export type SourcePreview={id:string;mode:string;source_label:string;files:number;bytes:number;ignored:number;reuse_existing:boolean;preview:{relative_path:string;size:number}[]};
