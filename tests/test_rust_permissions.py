"""Compare the ported symbolic algebra directly with the frozen Python algorithm."""
from dataclasses import dataclass
import json
from pathlib import Path
import random
import subprocess
import unittest

from thorlang.pureborrow import Access,Permission,OWNED,READONLY
from tests import test_rust_compiler as native


@dataclass(frozen=True)
class Source:
    path:Path


@dataclass(frozen=True)
class Node:
    source:Source
    start:int
    end:int


def node(file,index):return Node(Source(Path(f'file{file:03}')),index,index+1)


def evaluate(value):
    if isinstance(value,bool):return READONLY if value else OWNED
    if 'access' in value:
        file,index,param,path,aspect=value['access']
        return Permission('access',(Access(node(file,index),param,tuple(map(tuple,path)),aspect),))
    if 'type' in value:
        trait,file,index=value['type']
        return Permission('type',(trait,node(file,index)))
    if 'join' in value:
        a,b=map(evaluate,value['join']);return a|b
    if 'meet' in value:
        a,b=map(evaluate,value['meet']);return a&b
    if 'project' in value:
        expression,step,aspect=value['project'];return evaluate(expression).project(tuple(step),aspect)
    expression,substitutions=value['substitute']
    return evaluate(expression).substitute(lambda access:
        (READONLY if substitutions[access.index] else OWNED) if access.index<len(substitutions)
        else Permission('access',(access,)))


def encode(permission):
    kind,args=permission.kind,permission.args
    if kind=='constant':return [kind,args[0]]
    if kind=='access':
        access=args[0]
        return [kind,int(access.proc.source.path.name[4:]),access.proc.start,access.index,
                list(map(list,access.path)),access.aspect]
    if kind=='type':
        trait,ref=args
        return [kind,trait,int(ref.source.path.name[4:]),ref.start]
    if kind=='and':return [kind,*map(encode,args)]
    return [kind,list(map(encode,args))]


class RustPermissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result=subprocess.run(['cargo','build','--release','--locked','--offline','--manifest-path',
            str(native.ROOT/'rust/Cargo.toml'),'--example','permission_protocol'],
            capture_output=True,text=True,timeout=120)
        if result.returncode:raise RuntimeError(result.stdout+result.stderr)

    def test_generated_expressions_match_reference_canonical_permissions(self):
        rng=random.Random(906_311)
        def tree(depth):
            if depth<=0:
                if rng.randrange(4)==0:return bool(rng.randrange(2))
                if rng.randrange(3)==0:return {'type':[rng.choice(['pointer','sequence','big','not-big']),rng.randrange(3),rng.randrange(5)]}
                return {'access':[rng.randrange(3),rng.randrange(5),rng.randrange(4),
                    rng.choice([[],[['item']],[['field','data']],[['deref'],['field','value']]]),
                    rng.choice(['borrowed','location'])]}
            operation=rng.choice(['join','meet','project','substitute'])
            if operation in ('join','meet'):return {operation:[tree(depth-1),tree(depth-1)]}
            if operation=='project':return {operation:[tree(depth-1),rng.choice([['item'],['field','data'],['deref']]),rng.choice([None,'location','borrowed'])]}
            return {operation:[tree(depth-1),[bool(rng.randrange(2)) for _ in range(rng.randrange(5))]]}
        requests=[tree(rng.randrange(5)) for _ in range(750)]
        for length in (23,24,25):
            requests.append({'project':[{'access':[0,1,0,[['item']]*length,'borrowed']},['field','x'],'location']})
        expected=[encode(evaluate(request)) for request in requests]
        result=subprocess.run([str(native.ROOT/'rust/target/release/examples/permission_protocol')],
            input=json.dumps(requests),capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),expected)


if __name__=='__main__':unittest.main()
