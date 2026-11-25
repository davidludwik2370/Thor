"""Locate bounded native cache records without depending on their hash bucket."""
def entries(root, namespace):
    return list((root/'store-v2').glob('*/'+namespace+'-'+'?'*64+'.bin'))

def record(root, namespace, key):
    matches=entries(root,namespace)
    return next(p for p in matches if p.name==namespace+'-'+key+'.bin')
