export function nextBatchIds(choices: { id: string; processed: boolean; manual_modified: boolean }[], size: string): string[] | null {
  if (!/^[1-9]\d*$/.test(size) || Number(size) > 5000) return null;
  return choices.filter(choice => !choice.processed && !choice.manual_modified).slice(0, Number(size)).map(choice => choice.id);
}
