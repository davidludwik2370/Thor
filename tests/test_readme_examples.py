"""Compile the README's actual snippets; compare Thor/Odin behavior and errors."""
import html
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from urllib.parse import unquote, urlsplit

from tests import test_rust_compiler as base

ROOT=base.ROOT
README=ROOT/'README.md'
MANIFEST=ROOT/'tests/fixtures/readme-examples.json'
BLOCK=re.compile(r'<!-- readme-example: ([a-z0-9-]+):(thor|odin) -->\s*```odin\n(.*?)\n```',re.S)
CASES=json.loads(MANIFEST.read_text())['examples']


def displayed_output(code):
    lines=code.splitlines()
    # File examples may close main or a scoped block after the result comments.
    while lines and lines[-1].strip() in ('','}'):
        lines.pop()
    result=[]
    while lines:
        line=lines.pop().lstrip()
        if line=='//':result.append('')
        elif line.startswith('// '):result.append(line[3:])
        else:break
    if not result:raise ValueError('A runnable Thor example needs result comments after its final call')
    return '\n'.join(reversed(result))


def without_fences(text):
    return re.sub(r'^```[^\n]*\n.*?^```\s*$', '', text, flags=re.M|re.S)


def anchors(text):
    text=without_fences(text)
    found=set(re.findall(r'<a\s+id="([^"]+)"',text))
    counts={}
    for heading in re.findall(r'^#{1,6}\s+(.+?)\s*$',text,re.M):
        heading=html.unescape(re.sub(r'<[^>]+>','',heading))
        slug=re.sub(r'[^\w -]','',heading.lower()).replace(' ','-')
        number=counts.get(slug,0);counts[slug]=number+1
        found.add(slug+('-'+str(number) if number else ''))
    return found


def program(code,placement):
    tags=[];imports=[];body=[]
    for line in code.splitlines():
        if line.startswith('#+'):tags.append(line)
        elif line.startswith('import '):imports.append(line)
        else:body.append(line)
    if re.search(r'\bfmt\.',code) and not any(re.search(r'\bcore:fmt\b',line) for line in imports):
        imports.insert(0,'import "core:fmt"')
    header='\n'.join([*tags,'package main',*imports])+'\n'
    if placement=='file':return header+'\n'.join(body)+'\n'
    return header+'main :: proc() {\n'+'\n'.join('    '+line for line in body)+'\n}\n'


class ReadmeStructureTests(unittest.TestCase):
    def test_inventory_index_order_and_all_code_fences(self):
        text=README.read_text()
        blocks=BLOCK.findall(text)
        keys=[(identity,engine) for identity,engine,_ in blocks]
        expected=[(case['id'],engine) for case in CASES for engine in case['engines']]
        self.assertEqual(keys,expected)
        self.assertEqual(len(keys),len(set(keys)))
        self.assertEqual(len(re.findall(r'^```odin\s*$',text,re.M)),len(blocks),'Every Odin/Thor code fence needs a tested example marker')
        self.assertTrue(text.startswith('![Thor]('))
        self.assertLess(text.index('./thor-install --vscode'),text.index('## Thor extensions'))
        self.assertNotRegex(text,r'\bT(?:0[1-9]|1[0-9]|2[0-3])\b')
        index=text.split('## Thor extensions\n',1)[1].split('## Reading the examples',1)[0]
        features=[case for case in CASES if 'error' not in case]
        linked=re.findall(r'^- \[[^\]]+\]\(#([^\)]+)\)',index,re.M)
        self.assertEqual(linked,[case['section'] for case in features])
        actual_anchors=anchors(text)
        source={(identity,engine):code for identity,engine,code in blocks}
        for case in CASES:
            with self.subTest(example=case['id']):
                self.assertIn(case['section'],actual_anchors)
                if 'error' not in case:
                    self.assertEqual(displayed_output(source[case['id'],'thor']),case['stdout'])
                if case['engines']==['thor'] and 'error' not in case:
                    self.assertTrue(case.get('no_odin_reason'))

    def test_readme_and_moved_reference_links(self):
        failures=[]
        for source in [README,ROOT/'docs/language-guide.md',ROOT/'docs/compiler-guide.md']:
            text=without_fences(source.read_text())
            for target in re.findall(r'!?\[[^\]\n]*\]\(([^\s)]+)\)',text):
                url=urlsplit(target)
                if url.scheme or url.netloc:continue
                destination=(source.parent/unquote(url.path)).resolve() if url.path else source
                if not destination.exists():failures.append(f'{source.relative_to(ROOT)}: missing {target}');continue
                if url.fragment and destination.is_file() and destination.suffix=='.md':
                    if unquote(url.fragment) not in anchors(destination.read_text()):
                        failures.append(f'{source.relative_to(ROOT)}: missing anchor {target}')
        self.assertEqual(failures,[])

    def test_incoming_readme_anchors_are_preserved(self):
        known=anchors(README.read_text());failures=[]
        files=subprocess.check_output(['git','ls-files','-z','*.md'],cwd=ROOT).decode().split('\0')
        for name in filter(None,files):
            source=ROOT/name
            if not source.is_file():continue
            for target in re.findall(r'\]\(([^\s)]+)\)',without_fences(source.read_text(errors='replace'))):
                url=urlsplit(target)
                if url.scheme or url.netloc or not url.fragment or not url.path:continue
                if (source.parent/unquote(url.path)).resolve()==README:
                    if unquote(url.fragment) not in known:failures.append(f'{name}: {target}')
        self.assertEqual(failures,[])


class ReadmeExampleTests(unittest.TestCase):
    setUpClass=classmethod(base.RustCompilerTests.setUpClass.__func__)

    def exercise(self,case):
        blocks={(identity,engine):code for identity,engine,code in BLOCK.findall(README.read_text())}
        with tempfile.TemporaryDirectory(prefix='thor-readme-example-') as temporary:
            root=Path(temporary)
            for label,flags in [('checked',[]),('unchecked',base.UNCHECKED)]:
                outputs={}
                for engine in case['engines']:
                    modes=['required','normal','aggressive','override'] if engine=='thor' and case.get('comptime_modes') else [None]
                    for mode in modes:
                        directory=root/label/engine/(mode or 'default');directory.mkdir(parents=True)
                        source=directory/('main.thor' if engine=='thor' else 'main.odin')
                        source.write_text(program(blocks[case['id'],engine],case['placement']))
                        if engine=='thor':
                            command=[str(base.RUST),'build' if 'error' in case else 'run',str(source),
                                '--output-dir',str(directory/'generated'),'--cache-dir',str(root/'cache'),*flags]
                            if mode:command.append('--comptime='+mode)
                        else:
                            command=[os.environ.get('THOR_ODIN','odin'),'run',str(source),'-file',
                                '-collection:thor='+str(ROOT/'runtime'),'-out:'+str(directory/('sample.exe' if os.name=='nt' else 'sample')),*flags]
                        value=subprocess.run(command,cwd=directory,capture_output=True,text=True,timeout=90)
                        detail=f'{case["id"]} {engine} {label} {mode}:\n{source.read_text()}\n{value.stdout}{value.stderr}'
                        if 'error' in case:
                            self.assertNotEqual(value.returncode,0,detail)
                            self.assertIn(case['error'],value.stderr.lower(),detail)
                            self.assertNotIn('could not build',value.stderr,detail)
                            continue
                        self.assertEqual(value.returncode,0,detail)
                        self.assertEqual(value.stdout.strip(),case['stdout'],detail)
                        self.assertEqual(value.stderr,'',detail)
                        outputs[engine]=value.stdout
                if 'thor' in outputs and 'odin' in outputs:
                    self.assertEqual(outputs['thor'],outputs['odin'])


def make_case(case):
    def check(self):self.exercise(case)
    check.__name__='test_'+case['id'].replace('-','_')
    check.__doc__='README: '+case['id']
    return check


for _case in CASES:
    setattr(ReadmeExampleTests,'test_'+_case['id'].replace('-','_'),make_case(_case))


if __name__=='__main__':unittest.main()
