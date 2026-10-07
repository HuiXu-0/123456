import { parseMedia } from '@remotion/media-parser'
import { nodeReader } from '@remotion/media-parser/node'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
const here = path.dirname(fileURLToPath(import.meta.url))
const m = await parseMedia({
  src: path.join(here, 'out.mp4'),
  reader: nodeReader,
  fields: { durationInSeconds: true, dimensions: true, fps: true, videoCodec: true, audioCodec: true, tracks: true, size: true },
})
console.log('duration(s):', m.durationInSeconds)
console.log('dimensions :', m.dimensions)
console.log('fps        :', m.fps)
console.log('videoCodec :', m.videoCodec)
console.log('audioCodec :', m.audioCodec)
console.log('size(bytes):', m.size)
console.log('tracks     :', m.tracks.map(t => t.type + ':' + (t.codec||t.codecEnum||'?')).join(', '))
