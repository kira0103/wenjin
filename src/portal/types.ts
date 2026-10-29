export type PortalUser = { id:string;username:string;display_name:string;role:'user'|'admin';enabled:boolean|number;password_change_required:boolean|number;created_at:number;last_login_at:number|null };
export type PortalSession = {authenticated:boolean;mode:string;user:PortalUser|null};
export type WorkspaceState = {state:string;metrics?:{projects?:number;documents?:number|null;tasks?:number;active_tasks?:number};message?:string;updated_at?:number};
export type ManagedUser = PortalUser & {workspace:WorkspaceState};
