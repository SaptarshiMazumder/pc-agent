/* lottie-web ships types for its main entry only. The light player is the same API with the
 * expression and effect renderers left out, so it is declared against the package's own types
 * rather than re-typed here. */
declare module 'lottie-web/build/player/lottie_light' {
  import lottie from 'lottie-web'
  export * from 'lottie-web'
  export default lottie
}
