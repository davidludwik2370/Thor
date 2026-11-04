"""Explain checked-procedure failures at the fn call that imposed purity."""
from .sourcemap import Origin


def procedure_name(proc):
    declaration=proc.parent
    while declaration and declaration.kind=='Paren':declaration=declaration.parent
    names=declaration.group('names') if declaration and declaration.kind=='Declaration' else []
    return names[0].text if names else 'anonymous fn'


def call_contexts(graphs,origin,hints):
    """Use instantiation notes to avoid blaming inactive or unrelated callers."""
    if origin is None:return []
    for graph in graphs:
        targets={proc for values in graph.targets.values() for proc in values}
        candidates=[proc for proc in targets if str(proc.source.path)==origin.file
                    and proc.start<=origin.start<proc.end]
        if not candidates:continue
        owner=min(candidates,key=lambda proc:proc.end-proc.start)
        if graph.purity.pure(owner):return []
        chains=graph.chains(owner)
        if not chains:continue
        def covered(call):
            return any(site.file==str(call.source.path) and call.start<=site.start<call.end for site in hints)
        active=[chain for chain in chains if covered(chain[1][0])]
        if active:return active
        scores=[sum(covered(call) for call in path) for _,path in chains]
        best=max(scores)
        # No native caller evidence: retain each real root with the cause as a
        # related note, rather than silently assigning it to an arbitrary fn.
        return [chain for chain,score in zip(chains,scores) if score==best]
    return []


def contextualize(adapter,context,origin,messages,related):
    root,path=context
    call=path[0];name=procedure_name(root)
    primary=Origin(str(call.source.path),call.start,call.end,'pure-call-context')
    messages=[f"Pure function '{name}' cannot call '{call.role('callee').text}': {messages[0]}",*messages[1:]]
    visible=[node for node in path if not getattr(node.source,'native_inspected',False)]
    chain=' -> '.join([name,*[node.role('callee').text for node in visible]])
    messages.append('Pure call chain: '+chain+'.')
    notes=list(related)
    for node in visible[1:]:
        owner=procedure_name(next(parent for parent in node.ancestors() if parent.kind=='Procedure'))
        notes.append({'pos':adapter.locate(Origin(str(node.source.path),node.start,node.end)),
                      'message':f"'{owner}' calls '{node.role('callee').text}' while being checked for fn '{name}'."})
    covered=bool(origin and any(note['pos']['file']==origin.file and
                 note['pos']['offset']<=origin.start<note['pos']['end_offset'] for note in notes))
    if origin and not covered and not (primary.file==origin.file and primary.start<=origin.start<primary.end):
        notes.append({'pos':adapter.locate(origin),
                      'message':f"Underlying purity violation; this procedure is being checked because fn '{name}' reaches it."})
    signature=root.role('type')
    notes.append({'pos':adapter.locate(Origin(str(root.source.path),signature.start,signature.end)),
                  'message':f"fn '{name}' requires its entire call chain to be pure. Ordinary proc callers do not impose this requirement."})
    unique=[];seen=set()
    for note in notes:
        pos=note['pos'];key=(pos['file'],pos['offset'],pos['end_offset'],note['message'])
        if key not in seen:unique.append(note);seen.add(key)
    return primary,messages,unique
