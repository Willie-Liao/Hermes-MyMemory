/**
 * HERMES heading mode splits on `^## `, so a merged entry that still contains
 * two top-level ## headings round-trips back into two cards after Save.
 * Keep the first #/## heading; demote later ## → ### (and later # → ##).
 */
export function stabilizeHeadingEntry(entry: string): string {
  let seenTopHeading = false;
  return entry.replace(/^(#{1,2}) /gm, (match, hashes: string) => {
    if (!seenTopHeading) {
      seenTopHeading = true;
      return match;
    }
    if (hashes === '##') return '### ';
    return '## ';
  });
}

export function stripSectionMarker(text: string): string {
  return text.replace(/§/g, '');
}
