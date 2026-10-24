/** Read complete SSE events as they arrive. Owns and releases the response reader. */
export async function consumeSse(response, onEvent, maxEventChars = 1024 * 1024) {
  if (!Number.isSafeInteger(maxEventChars) || maxEventChars <= 0) throw new RangeError('Invalid SSE event limit');
  if (!response.body) throw new Error('The server returned no event stream');
  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8', { fatal: true });
  let pending = '', event = 'message', data = [], size = 0, eof = false;

  function line(value) {
    if (value.length > maxEventChars) throw new RangeError('SSE event exceeds the buffer limit');
    if (value === '') {
      const name = event;
      const payload = data.join('\n');
      const present = data.length > 0;
      event = 'message'; data = []; size = 0;
      if (present) onEvent({ event: name, data: payload });
      return present && name === 'done';
    }
    const colon = value.indexOf(':');
    const field = colon < 0 ? value : value.slice(0, colon);
    let content = colon < 0 ? '' : value.slice(colon + 1);
    if (content.startsWith(' ')) content = content.slice(1);
    if (field === 'event') event = content;
    if (field === 'data') {
      size += content.length + 1;
      if (size > maxEventChars) throw new RangeError('SSE event exceeds the buffer limit');
      data.push(content);
    }
    return false;
  }

  try {
    for (;;) {
      const result = await reader.read();
      eof = result.done;
      pending += decoder.decode(result.value, { stream: !eof });
      const breaks = /\r\n|\r|\n/g;
      let start = 0, match;
      while ((match = breaks.exec(pending))) {
        // A CR at a chunk boundary may be the first half of CRLF.
        if (!eof && match[0] === '\r' && match.index + 1 === pending.length) break;
        if (line(pending.slice(start, match.index))) return;
        start = breaks.lastIndex;
      }
      pending = pending.slice(start);
      if (pending.length + size > maxEventChars) throw new RangeError('SSE event exceeds the buffer limit');
      if (eof) break; // An incomplete final event is not dispatched.
    }
  } finally {
    if (!eof) await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
