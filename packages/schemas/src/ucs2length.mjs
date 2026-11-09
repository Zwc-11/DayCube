// Length in Unicode code points, as JSON Schema's minLength and maxLength count it: ajv's
// ajv/dist/runtime/ucs2length (MIT), as an ES module for the precompiled validators.mjs.
export function ucs2length(str) {
  const len = str.length;
  let length = 0;
  let pos = 0;
  while (pos < len) {
    length++;
    const value = str.charCodeAt(pos++);
    if (value >= 0xd800 && value <= 0xdbff && pos < len && (str.charCodeAt(pos) & 0xfc00) === 0xdc00) pos++;
  }
  return length;
}
