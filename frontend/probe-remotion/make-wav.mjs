// 生成 3s 440Hz 正弦单声道 WAV（无依赖），模拟一段 edge-tts 旁白音轨
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
const here = path.dirname(fileURLToPath(import.meta.url))
const sr = 44100, sec = 3, n = sr*sec
const data = Buffer.alloc(n*2)
for (let i=0;i<n;i++){ data.writeInt16LE(Math.round(Math.sin(2*Math.PI*440*i/sr)*8000), i*2) }
const h = Buffer.alloc(44)
h.write('RIFF',0); h.writeUInt32LE(36+data.length,4); h.write('WAVE',8); h.write('fmt ',12)
h.writeUInt32LE(16,16); h.writeUInt16LE(1,20); h.writeUInt16LE(1,22); h.writeUInt32LE(sr,24)
h.writeUInt32LE(sr*2,28); h.writeUInt16LE(2,32); h.writeUInt16LE(16,34); h.write('data',36); h.writeUInt32LE(data.length,40)
fs.writeFileSync(path.join(here,'narration.wav'), Buffer.concat([h,data]))
console.log('wav written, 3.0s 440Hz')
