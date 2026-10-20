export function debugSample(text: string, limit = 800) {
  const characters = Array.from(text);
  if (characters.length <= limit) return { text, truncated: false, completeLine: true };
  const prefix = characters.slice(0, limit).join('');
  const boundary = prefix.lastIndexOf('\n');
  if (boundary >= prefix.length / 3) return { text: prefix.slice(0, boundary + 1), truncated: true, completeLine: true };
  return { text: prefix, truncated: true, completeLine: false };
}
