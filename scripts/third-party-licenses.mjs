import {readFile} from 'node:fs/promises';

export const frontendLicenseFile = 'THIRD_PARTY_LICENSES.txt';
const root = new URL('../', import.meta.url);

/** Keep the distributed browser code and fonts accompanied by their upstream terms. */
export async function frontendLicenses() {
 const sections = [
  'Third-party software in the Reader browser assets',
  'These notices apply to the named third-party components, not to the original application code.\n'
 ];
 for (const [name, files, copyright, source] of [
  ['katex', ['LICENSE'], '', 'https://github.com/KaTeX/KaTeX'],
  ['dompurify', ['LICENSE', 'LICENSE-MPL'], '(c) Cure53 and other contributors', 'https://github.com/cure53/DOMPurify'],
 ]) {
  const directory = new URL(`node_modules/${name}/`, root);
  const metadata = JSON.parse(await readFile(new URL('package.json', directory), 'utf8'));
  sections.push(`${name} ${metadata.version}\nDeclared license: ${metadata.license}\n${source}${copyright ? '\n' + copyright : ''}`);
  for (const file of files) sections.push(`${name}/${file}\n\n${await readFile(new URL(file, directory), 'utf8')}`);
 }
 return sections.join('\n\n' + '='.repeat(72) + '\n\n') + '\n';
}
