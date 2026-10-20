export function sourceRange(body: string, selected = '') {
  const offset = selected ? body.indexOf(selected) : -1;
  if (offset < 0) return { start: 0, end: Array.from(body).length };
  const start = Array.from(body.slice(0, offset)).length;
  return { start, end: start + Array.from(selected).length };
}
