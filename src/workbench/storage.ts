// Desktop preferences retain their existing keys. Web accounts use isolated tab storage.
let scope = '';
const prefix = (identity: string) => `wenjin-user:${identity}:`;
const store = () => scope ? globalThis.sessionStorage : globalThis.localStorage;

export function setStorageAccount(identity: string) { scope = identity; }
export function currentStorageAccount() { return scope; }
export function clearStorageAccount(identity: string) {
  const storage = globalThis.sessionStorage;
  const keys = Array.from({length:storage.length}, (_, index) => storage.key(index));
  for (const key of keys) if (key?.startsWith(prefix(identity))) storage.removeItem(key);
}
export const workspaceStorage = {
  getItem: (key: string) => store().getItem((scope ? prefix(scope) : '') + key),
  setItem: (key: string, value: string) => store().setItem((scope ? prefix(scope) : '') + key, value),
  removeItem: (key: string) => store().removeItem((scope ? prefix(scope) : '') + key),
};
