/** Build a small display copy without changing the attachment sent to agentd. The full file is
 *  still read separately for the wire payload, but it never becomes an <img> source and can be
 *  released after the send finishes. */
export async function createLocalImageThumbnail(
  file: File,
  maxPixels = 256,
): Promise<string | undefined> {
  if (!file.type.startsWith('image/') || typeof createImageBitmap !== 'function') return undefined

  let bitmap: ImageBitmap | undefined
  try {
    bitmap = await createImageBitmap(file)
    if (!bitmap.width || !bitmap.height) return undefined

    const limit = Math.max(1, Math.round(maxPixels))
    const scale = Math.min(1, limit / Math.max(bitmap.width, bitmap.height))
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(bitmap.width * scale))
    canvas.height = Math.max(1, Math.round(bitmap.height * scale))

    const context = canvas.getContext('2d')
    if (!context) return undefined
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
    return canvas.toDataURL('image/webp', 0.82)
  } catch {
    // Unsupported image formats keep their filename chip; never fall back to the full data URL.
    return undefined
  } finally {
    bitmap?.close()
  }
}
