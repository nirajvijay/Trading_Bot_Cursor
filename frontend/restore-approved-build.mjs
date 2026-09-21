import { cpSync, rmSync, readFileSync } from 'node:fs'
import { createHash } from 'node:crypto'

// The editable source has not yet been recovered to match the approved UI.
// Production must serve the preserved release until that recovery is verified.
const approved = new URL('../Reference/live-release-auto-login-button-20260916121611/frontend/dist/', import.meta.url)
const expected = {
  'index.html': '3670385dad25e77f8e29e4461d2ea260e0bc6738845e744a791e8f0a72830af5',
  'assets/index-DlyfxDlt.js': 'ff2e03b2ffbb9792746c342fa503e30316301527534830b0921fc7ccab15b570',
  'assets/index-BNDqV7g7.css': '650a7c94160d7638f42bd3b2a4440e9054d29449c6b13fdd23e6780766689e7b',
}
for (const [file, hash] of Object.entries(expected)) {
  if (createHash('sha256').update(readFileSync(new URL(file, approved))).digest('hex') !== hash) {
    throw new Error(`Approved release checksum mismatch: ${file}`)
  }
}
const output = new URL('./dist/', import.meta.url)
rmSync(output, { recursive: true, force: true })
cpSync(approved, output, { recursive: true })
console.log('Verified and restored approved auto-login-button-20260916121611 assets. Source edits require build:source-preview and visual recovery before production use.')
