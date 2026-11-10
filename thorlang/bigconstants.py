"""Read-only static limbs and their allocation-free native view headers."""
import hashlib
import json
from fractions import Fraction
from . import bigvalues


class NotConstant(Exception):pass


class BigConstants:
    def __init__(self,big):
        self.big=big
        self.s=big.s
        self.e=big.e
        self.payloads={}
        self.busy=set()
        self.evaluating=set()

    def evaluate(self,node,kind=None):
        if node is None or node in self.evaluating:raise NotConstant()
        self.evaluating.add(node)
        try:return self._evaluate(node,kind)
        finally:self.evaluating.remove(node)

    def _evaluate(self,node,kind):
        source=node.source;resolver=source.resolver
        if node in source.big.constants.payloads:return source.big.constants.payloads[node]
        if node.kind=='Paren':return self.evaluate(node.role('expr'),kind)
        if node.text in {'true','false'} and resolver.symbol(node) is None:return dict(kind='bool',value=node.text=='true')
        if node.kind=='CompileSwitchExpr':
            problem=self.validate_cases(node)
            if problem:raise ValueError(problem)
            default=None
            for case in node.data['cases']:
                if not case['matches']:default=case;continue
                selected=self.case(node,case)
                if selected is None:
                    if node.role('selector'):raise NotConstant()
                    selected=any(self.evaluate(source.at(span,node))==dict(kind='bool',value=True) for span in case['matches'])
                if selected:return self.evaluate(source.at(case['result'],node),kind)
            if default:return self.evaluate(source.at(default['result'],node),kind)
            raise NotConstant()
        if node.kind=='Literal' and node.data['op'] in {'integer','float'}:
            return bigvalues.packet(kind or 'big_rational',bigvalues.literal(node.text))
        if node.kind in {'Ident','Selector'}:
            if source.big.property(node):
                base=self.evaluate(node.role('expr'))
                if base['kind']!='big_rational':raise NotConstant()
                value=bigvalues.number(base)
                return bigvalues.packet('big_integer',getattr(value,node.role('field').text))
            symbol=resolver.symbol(node)
            if not(symbol and symbol.value and symbol.declaration.kind=='Declaration' and not symbol.declaration.data.get('mutable',True)):
                raise NotConstant()
            annotation=resolver.type_node(symbol.annotation).kind if symbol.annotation else None
            if annotation and annotation not in {'big_integer','big_rational'}:raise NotConstant()
            return self.evaluate(symbol.value,annotation or kind)
        if node.kind=='Call':
            target=source.big.constructor(node)
            if not target:
                name=source.big.integration.builtin(node)
                typ=source.big.integration.result(node) if name else None
                chosen=kind or (typ.kind if typ and typ.kind in {'big_integer','big_rational'} else None)
                if not name or not chosen:raise NotConstant()
                args=node.group('args')
                numbers=[]
                for arg in args:
                    shape=resolver.type_of(arg)
                    if shape.kind in {'big_integer','big_rational'} and shape.kind!=chosen:raise ValueError('Integer/Rational operands must have matching types.')
                    if shape.kind not in {'big_integer','big_rational'} and not resolver.membership_untyped(arg):raise NotConstant()
                    numbers.append(bigvalues.number(self.evaluate(arg,chosen)))
                if name=='abs' and len(numbers)==1:result=abs(numbers[0])
                elif name in {'min','max'} and numbers:result=(min if name=='min' else max)(numbers)
                elif name=='clamp' and len(numbers)==3:result=min(max(numbers[0],numbers[1]),numbers[2])
                else:raise NotConstant()
                return bigvalues.packet(chosen,result)
            args=node.group('args');named={};positional=[]
            for arg in args:
                if arg.kind=='FieldValue':
                    name=arg.role('field').text
                    if name in named:raise ValueError('Repeated big-number constructor argument.')
                    named[name]=arg.role('value')
                else:positional.append(arg)
            if any(name not in {'value','numerator','denominator'} for name in named):raise NotConstant()
            if target.kind=='big_integer' or len(args)==1:
                values=positional+[v for k,v in named.items() if k=='value']
                if len(values)!=1:raise ValueError('A numeric constructor requires one value or a numerator/denominator pair.')
                value=self.evaluate(values[0],target.kind)
                if target.kind=='big_integer' and value['kind']=='big_rational':raise ValueError('Integer/Rational constructors require exact integer inputs or text; convert Rational values explicitly first.')
                # Constructing Rational from Integer is explicit and exact.
                return bigvalues.packet(target.kind,bigvalues.number(value))
            values=[]
            for index,name in enumerate(['numerator','denominator']):
                if name in named:values.append(named[name])
                elif index<len(positional):values.append(positional[index])
                else:raise ValueError('Rational requires a numerator and denominator.')
            if len(args)!=2:raise ValueError('Rational requires a numerator and denominator.')
            contents=[self.evaluate(v,'big_integer') for v in values]
            if any(v['kind']!='big_integer' for v in contents):raise ValueError('Rational numerator and denominator require Integer or native integer inputs.')
            n,d=[bigvalues.number(v) for v in contents]
            if not d:raise ValueError('A Rational denominator must not be zero.')
            return bigvalues.packet('big_rational',Fraction(n,d))
        if node.kind in {'Unary','Binary'}:
            op=node.data['op']
            if node.kind=='Unary':
                value=self.evaluate(node.role('expr'),kind)
                a=bigvalues.number(value)
                if op=='+':result=a
                elif op=='-':result=-a
                elif op=='~' and value['kind']=='big_integer':result=~a
                else:raise NotConstant()
                return bigvalues.packet(value['kind'],result)
            left,right=node.role('left'),node.role('right')
            shapes=[resolver.type_of(v).kind for v in [left,right]]
            actual=[k for k in shapes if k in {'big_integer','big_rational'}]
            chosen=kind or (actual[0] if actual else None)
            if not chosen:raise NotConstant()
            for value,shape in zip([left,right],shapes):
                if shape in {'big_integer','big_rational'} and shape!=chosen:
                    raise ValueError('Integer/Rational operands must have matching types; use an explicit conversion.')
                if shape not in {'big_integer','big_rational'} and not resolver.membership_untyped(value):raise NotConstant()
            x=self.evaluate(left,chosen);y=self.evaluate(right,chosen)
            a,b=bigvalues.number(x),bigvalues.number(y)
            if op in {'==','!=','<','<=','>','>='}:
                result={'==':lambda:a==b,'!=':lambda:a!=b,'<':lambda:a<b,'<=':lambda:a<=b,'>':lambda:a>b,'>=':lambda:a>=b}[op]()
                return dict(kind='bool',value=result)
            if op in {'&','|','~','&~','<<','>>'} and chosen!='big_integer':raise ValueError('Rational does not support bitwise operators or shifts.')
            if op in {'/','%','%%'} and not b:raise ValueError('A big-number divisor must not be zero.')
            if op=='+':result=a+b
            elif op=='-':result=a-b
            elif op=='*':result=a*b
            elif op=='/':result=int(Fraction(a,b)) if chosen=='big_integer' else a/b
            elif op=='%':result=a-int(Fraction(a,b))*b
            elif op=='%%':result=a%b
            elif op=='&':result=a&b
            elif op=='|':result=a|b
            elif op=='~':result=a^b
            elif op=='&~':result=a&~b
            elif op in {'<<','>>'}:
                if b<0:raise ValueError('Integer shift counts must be nonnegative.')
                if op=='<<' and a and b>8*16*1024*1024:raise ValueError('Compile-time result exceeds 16 MiB.')
                result=a<<b if op=='<<' else a>>b
            elif op=='**':
                if Fraction(b).denominator!=1:raise ValueError('Rational powers require whole exponents.')
                b=int(b)
                if chosen=='big_integer' and b<0:raise ValueError('Integer powers require a nonnegative exponent; use Rational for negative powers.')
                if not a and b<0:raise ValueError('A big-number divisor must not be zero.')
                if abs(a)>1 and abs(b)>8*16*1024*1024:raise ValueError('Compile-time result exceeds 16 MiB.')
                result=a**b
            else:raise NotConstant()
            return bigvalues.packet(chosen,result)
        if node.kind in {'Ternary','WhenExpr'}:
            cond=self.evaluate(node.role('cond'))
            if cond['kind']!='bool':raise NotConstant()
            return self.evaluate(node.role('x' if cond['value'] else 'y'),kind)
        raise NotConstant()

    def render(self,node,hint):
        requested=self.big.expected(hint)
        kind=requested.kind if requested and requested.kind in {'big_integer','big_rational'} else None
        shape=self.big.shape(node).kind
        concrete=shape in {'big_integer','big_rational'} or node.kind=='Binary' and any(self.big.shape(node.role(k)).kind in {'big_integer','big_rational'} for k in ['left','right'])
        if node.kind=='CompileSwitchExpr':
            concrete=any(self.big.shape(self.s.at(case['result'],node)).kind in {'big_integer','big_rational'} for case in node.data['cases'])
        if not kind and not concrete:return None
        try:value=self.evaluate(node,kind)
        except NotConstant:return None
        except (ValueError,ZeroDivisionError,OverflowError) as exc:return self.big.error(node,str(exc))
        self.remember(node,value)
        return str(value['value']).lower() if value['kind']=='bool' else self.view(value,node)

    def readonly(self,node):
        while node:
            symbol=node.source.resolver.symbol(node)
            if (symbol and symbol.declaration.kind=='Declaration' and not symbol.declaration.data.get('mutable',True)
                    and node.source.resolver.type_of(node).kind in {'big_integer','big_rational'}):return True
            if node.kind not in {'Selector','Index','Paren'}:return False
            node=node.role('expr')
        return False

    def case(self,node,case):
        selector=node.role('selector')
        if not selector or self.big.shape(selector).kind not in {'big_integer','big_rational'}:return None
        try:
            selected=self.evaluate(selector)
            kind=selected['kind'];number=bigvalues.number(selected)
            for span in case['matches']:
                match=self.s.at(span,node)
                if match.kind=='Binary' and match.data['op'] in {'..','..<','..='}:
                    low=bigvalues.number(self.evaluate(match.role('left'),kind))
                    high=bigvalues.number(self.evaluate(match.role('right'),kind))
                    if low<=number and (number<=high if match.data['op']=='..=' else number<high):return True
                elif number==bigvalues.number(self.evaluate(match,kind)):return True
            return not case['matches']
        except (NotConstant,ValueError,ZeroDivisionError,OverflowError):return None

    def validate_cases(self,node):
        selector=node.role('selector')
        if not selector or self.big.shape(selector).kind not in {'big_integer','big_rational'}:return None
        kind=self.big.shape(selector).kind
        intervals=[]
        for case in node.data['cases']:
            for span in case['matches']:
                match=self.s.at(span,node)
                try:
                    if match.kind=='Binary' and match.data['op'] in {'..','..<','..='}:
                        low,high=[bigvalues.number(self.evaluate(match.role(k),kind)) for k in ['left','right']]
                        closed=match.data['op']=='..='
                        if low>high or low==high and not closed:return 'A switch case range must not be empty or reversed.'
                    else:low=high=bigvalues.number(self.evaluate(match,kind));closed=True
                except (NotConstant,ValueError,ZeroDivisionError,OverflowError):continue
                for a,b,inclusive in intervals:
                    lower,upper=max(low,a),min(high,b)
                    if lower<upper or lower==upper and (lower<high or closed) and (lower<b or inclusive):
                        return 'Duplicate or overlapping constant switch cases.'
                intervals.append((low,high,closed))
        return None

    def view(self,value,node):
        number=bigvalues.number(value)
        if value['kind']=='big_rational':
            if not number:return self.big.module()+'.Rational{}'
            numerator=bigvalues.packet('big_integer',number.numerator)
            denominator=bigvalues.packet('big_integer',number.denominator)
            return self.big.module()+'.compiler_static_rational('+self.view(numerator,node)+','+self.view(denominator,node)+')'
        if not number:return self.big.module()+'.Integer{}'
        data=[];magnitude=abs(number)
        while magnitude:
            data.append(magnitude&((1<<63)-1));magnitude>>=63
        # Odin's backend initializes undersized input headers, even in copy.
        # Reserve its minimum four limbs so borrowed rodata never resizes.
        data.extend([0]*max(0,4-len(data)))
        digest=hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()[:20]
        name=self.s.prefix+'big_data_'+digest
        key=self.s.root,name
        if key not in self.e.helpers:
            code=(f'\n@(private="file",rodata)\n{name}:=[{len(data)}]{self.big.module()}.compiler_digit{{'+','.join(map(str,data))+'}\n'
                  f'#assert({self.big.module()}.compiler_digit_bits==63,"Unsupported bigint limb layout for constant data")\n')
            self.e.helpers[key]=self.big.mapped(node,code,'support')
        return self.big.module()+'.compiler_static_integer('+name+'[:],'+str(number<0).lower()+')'

    def remember(self,node,data):
        self.payloads[node]=data

    def declaration(self,node):
        if node in self.busy or node.kind!='Declaration' or node.data.get('mutable',True):return None
        values=node.group('values')
        hint=node.role('type')
        kind=self.s.resolver.type_node(hint).kind if hint else None
        for value in values:
            self.render(value,self.big.type_hint(kind) if kind in {'big_integer','big_rational'} else None)
        if not any(value in self.payloads and bigvalues.contains(self.payloads[value]) for value in values):return None
        separator=node.data['roles'].get('separator')
        if not separator:return self.big.error(node,'The frontend did not provide this numeric constant declaration separator.')
        self.busy.add(node)
        edit=(*separator,'=')
        self.s.edits.append(edit)
        try:return self.s._render(node)
        finally:
            self.s.edits.remove(edit)
            self.busy.remove(node)
