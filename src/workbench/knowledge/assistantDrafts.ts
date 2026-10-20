export function readQuestion(key:string){try{return localStorage.getItem(key)||'';}catch{return '';}}
