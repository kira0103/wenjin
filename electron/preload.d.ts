interface Window {
  desktop: {
    serviceStatus?(): Promise<{stage:string;message:string;elapsed:number}>;
    setTheme?(theme:'light'|'dark'):Promise<void>;
    workspaceInfo(): Promise<{dataDir:string;configFile:string;changing:boolean;versionDependent:boolean}>;
    openWorkspace(): Promise<void>;
    changeWorkspace(mode:'migrate'|'switch'): Promise<{changed:boolean;dataDir?:string}>;
    onWorkspaceProgress(callback:(value:{stage:string;copied?:number;total?:number;error?:string})=>void):()=>void;
    restoreBackup(): Promise<{restored:boolean;destination?:string}>;
    request(request: { path: string; method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'; body?: unknown; contentType?: string }): Promise<{ status: number; contentType: string; body: any }>;
    streamKnowledge(body: unknown, callback: (event: { type: string; text?: string; message?: string; answer?: string; evidence?: any[]; warnings?: string[]; retrieval_run_id?: string }) => void): { promise: Promise<unknown>; cancel(): void };
    pickPdf(): Promise<Array<{ name: string; data: ArrayBuffer }>>;
    pickDocuments(): Promise<Array<{ name: string; data: ArrayBuffer }>>;
    chooseLiteratureSource(mode:'files'|'directory'): Promise<null|{id:string;mode:string;source_label:string;files:number;bytes:number;ignored:number;reuse_existing:boolean;preview:{relative_path:string;size:number}[]}>;
    saveFile(file: { filename: string; data: ArrayBuffer }): Promise<boolean>;
    openExternal(url: string): Promise<void>;
    onServiceReady(callback: (url: string) => void): () => void;
  };
}
