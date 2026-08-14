/* `md.js` is borrowed from templates/_borrowed/ and imported for its side effect (it assigns
 * window.MD). It is plain JavaScript with no types of its own, so TypeScript needs to be told the
 * import is legal. The shape of what it assigns is declared in src/markdown/md.ts, next to the
 * code that reads it. */
declare module '*/_borrowed/md.js'
