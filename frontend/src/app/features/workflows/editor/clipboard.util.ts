/** Copy text to the clipboard, falling back to the legacy execCommand path on
 *  plain-HTTP LAN deployments (navigator.clipboard needs a secure context). */
export function copyText(text: string, done: () => void): void {
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(done, () => legacyCopy(text, done));
  } else {
    legacyCopy(text, done);
  }
}

function legacyCopy(text: string, done: () => void): void {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  try {
    if (document.execCommand('copy')) done();
  } finally {
    document.body.removeChild(ta);
  }
}
