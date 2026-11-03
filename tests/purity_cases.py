"""Shared rejection inventory for real CLI and stdio TLS tests."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Case:
    name: str
    body: str
    message: str
    needle: str
    prefix: str = ''
    params: str = 'n:int'
    args: str = '7'
    result: str = 'int'

    def source(self, kind='fn', mode='runtime'):
        decl=f'bad::{kind}({self.params})->{self.result}{{\n    /* 😀 */ {self.body}\n}}\n'
        call=f'bad({self.args})'
        usage='main::proc(){_='+call+'}\n' if mode=='runtime' else 'VALUE::'+('#run ' if mode=='run' else '')+call+'\nmain::proc(){_=VALUE}\n'
        return '// 😀 purity inventory\npackage main\n'+self.prefix+'\n'+decl+usage

    def offset(self,text):return text.encode().index(self.needle.encode(),text.encode().index(b'bad::'))


EFFECTS=[
    Case('global_read','return state+n','outside the function','state',prefix='state:int'),
    Case('global_write','state+=n;return n','outside the function','state',prefix='state:int'),
    Case('immutable_global','return state+n','outside the function','state',prefix='state:>3'),
    Case('context','_=context;return n','ambient context','context'),
    Case('file_identity','_=#file;return n','Source-location-dependent','#file'),
    Case('line_identity','_=#line;return n','Source-location-dependent','#line'),
    Case('directory_identity','_=#directory;return n','Source-location-dependent','#directory'),
    Case('caller_identity','return n','Source-location-dependent','#caller_location',params='n:int,loc:=#caller_location'),
    Case('project_identity','_=ODIN_BUILD_PROJECT_NAME;return n','Source-location-dependent','ODIN_BUILD_PROJECT_NAME'),
    Case('address','local:=n;_= &local;return n','raw addresses','&local'),
    Case('uninitialized','x:int=---;return n+x','uninitialized storage','---'),
    Case('aggregate_transmute','x:=transmute([8]u8)f64(n);return n+len(x)','Pure transmute','transmute'),
    Case('pointer_conversion','_=rawptr(nil);return n','convert values to pointers','rawptr(nil)'),
    Case('local_any','v:any=n;_=v;return n','Pure local storage','v:any'),
    Case('local_map','v:map[int]int;return n+len(v)','Pure local storage','v:map'),
    Case('local_set','v:=set[int]{n};return len(v)','Pure local storage','v:='),
    Case('allocator','_=make([]int,n,context.allocator);return n','select an allocator','make'),
    Case('record_formatter','_=f"{Record{n}}";return n','deterministic scalar/string','Record{n}',prefix='Record::struct{x:int}'),
    Case('io','fmt.println(n);return n','performs I/O','fmt.println',prefix='import "core:fmt"'),
    Case('foreign','return int(external(i32(n)))','foreign','external',prefix='foreign import libc "system:c"\nforeign libc{external::proc "c"(n:i32)->i32---}'),
    Case('indirect','cb:=identity;return cb(n)','indirect','cb(n)',prefix='identity::proc(n:int)->int{return n}'),
    Case('intrinsic','return int(intrinsics.read_cycle_counter())+n','not an eligible pure operation','intrinsics.read_cycle_counter',prefix='import "base:intrinsics"'),
    Case('borrow_write','input[0]+=1;return input[0]','borrowed from an input','input[0]+=',params='input:[]int',args='[]int{7}'),
    Case('borrow_copy','copy(input,[]int{9});return input[0]','borrowed from an input','copy(',params='input:[]int',args='[]int{7}'),
    Case('borrow_copy_reordered','local:=make([]int,1);copy(src=local,dst=input);return input[0]','borrowed from an input','copy(',params='input:[]int',args='[]int{7}'),
    Case('borrow_delete','delete(array=input);return 0','borrowed from an input','delete(',params='input:[]int',args='[]int{7}'),
    Case('borrow_generic_component','local:=input.denominator;delete(local);return 0','borrowed from an input','delete(',
         prefix='ENABLE_BIG::Integer(0)\nRecord::struct{denominator:[]int}',params='input:$T',args='Record{[]int{7}}'),
    Case('borrow_generic_component_join','local:=input.denominator;local=input.other;delete(local);return 0','borrowed from an input','delete(',
         prefix='ENABLE_BIG::Integer(0)\nRecord::struct{denominator,other:[]int}',params='input:$T',args='Record{[]int{7},[]int{8}}'),
    Case('borrow_integer_field','local:=input;local.value+=1;return 0','borrowed from an input','local.value+=',
         prefix='Box::struct{value:Integer}',params='input:Box',args='Box{Integer(7)}'),
    Case('borrow_rational_field','local:=input;local.value+=1;return 0','borrowed from an input','local.value+=',
         prefix='Box::struct{value:Rational}',params='input:Box',args='Box{Rational(7,3)}'),
    Case('integer_representation','_=input._value;return 0','implementation storage','input._value',params='input:$T',args='Integer(7)'),
    Case('rational_representation','_=input._numerator;return 0','implementation storage','input._numerator',params='input:$T',args='Rational(7,3)'),
    Case('range_reference','for &item in input{item+=1};return input[0]','iterate by mutable reference','for &',params='input:[]int',args='[]int{7}'),
    Case('type_switch_reference','local:=input;switch &value in local{case int:_=value;case bool:_=value};return 0',
         'type-switch value by mutable reference','switch &',prefix='Choice::union{int,bool}',params='input:Choice',args='Choice(7)'),
    Case('default_global','return n','outside the function','state',prefix='state:int',params='n:int=state',args=''),
    Case('default_context','return n','ambient context','context',params='n:int=context.user_index',args=''),
]

TYPES=[
    Case('input_pointer','return 0','Unsupported pure value type','^int',params='input:^int',args='nil'),
    Case('input_multi_pointer','return 0','Unsupported pure value type','[^]int',params='input:[^]int',args='nil'),
    Case('input_raw_pointer','return 0','Unsupported pure value type','rawptr',params='input:rawptr',args='nil'),
    Case('input_typeid','return 0','Unsupported pure value type','typeid',params='input:typeid',args='int'),
    Case('input_any','return 0','Unsupported pure value type','any',params='input:any'),
    Case('input_map','return 0','Unsupported pure value type','map[int]int',params='input:map[int]int',args='nil'),
    Case('input_dynamic','return 0','Unsupported pure value type','[dynamic]int',params='input:[dynamic]int',args='nil'),
    Case('input_proc','return 0','Unsupported pure value type','proc()->int',params='input:proc()->int',args='nil'),
    Case('input_cstring','return 0','Unsupported pure value type','cstring',params='input:cstring',args='nil'),
    Case('input_cstring16','return 0','Unsupported pure value type','cstring16',params='input:cstring16',args='nil'),
    Case('input_string16','return 0','Unsupported pure value type','string16',params='input:string16',args='"text"'),
    Case('nested_pointer','return 0','Unsupported pure value type','Box',prefix='Box::struct{items:[]^int}',params='input:Box',args='Box{}'),
    Case('union_pointer','return 0','Unsupported pure value type','Choice',prefix='Choice::union{int,^int}',params='input:Choice',args='Choice(1)'),
    Case('recursive_slice','return 0','recursive reference-containing types','Node',prefix='Node::struct{children:[]Node}',params='input:Node',args='Node{}'),
    Case('result_pointer','return nil','Unsupported pure value type','^int',result='^int'),
    Case('result_typeid','return int','Unsupported pure value type','typeid',result='typeid'),
    Case('result_dynamic','return nil','Unsupported pure value type','[dynamic]int',result='[dynamic]int'),
    Case('result_any','return nil','Unsupported pure value type','any',result='any'),
]
