"""Versioned research configurations; not clinically validated alarm rules."""
from datetime import datetime
from fractions import Fraction
from model import normalize, Q, jnum, dec, evaluate
import z3 as z

def run_dynamic(snapshots,config=None):
    if config is None:return {'status':'unconfigured','triggered':None,'message':'动态规则未配置'}
    required=['id','version','identity','max_interval_hours','metrics','trigger']
    if any(k not in config for k in required) or config['identity']!='research_only':raise ValueError('explicit versioned research-only configuration required')
    if config['trigger']['operator'] not in ['any','all','weighted_at_least']:raise ValueError('unsupported trigger')
    if not config['metrics'] or Fraction(str(config['max_interval_hours']))<=0:raise ValueError('positive window and nonempty metrics required')
    for rule in config['metrics']:
        if rule['direction'] not in ['rise','fall'] or rule['measure'] not in ['absolute','percent'] or Fraction(str(rule['threshold']))<0 or Fraction(str(rule['weight']))<0:raise ValueError('invalid dynamic metric rule')
    if len(snapshots)<2:return {'status':'insufficient_history','triggered':None,'config':config['id']+'@'+config['version']}
    a,b=snapshots[-2:]
    validations=[]
    for snap in [a,b]:
        try:validations.append(evaluate(snap))
        except ValueError as e:validations.append({'status':'invalid_evidence','reason':str(e)})
    if any(v['status']!='evaluated' for v in validations):return {'status':'invalid_evidence','triggered':None,'validation':validations}
    if a['patient_id']!=b['patient_id']:raise ValueError('mixed patients')
    dt=lambda v:datetime.fromisoformat(v.replace('Z','+00:00'))
    elapsed=dt(b['observed_at'])-dt(a['observed_at'])
    hours=Fraction(elapsed.days*86400*1000000+elapsed.seconds*1000000+elapsed.microseconds,3600*1000000)
    if hours<=0 or hours>Fraction(str(config['max_interval_hours'])):return {'status':'outside_comparison_window','triggered':None}
    details=[];unknown=False;hits=[];weight=Fraction(0)
    for rule in config['metrics']:
        k=rule['field'];row={'field':k,'group':rule.get('group','general'),'triggered':None}
        try:
            aa=normalize(a['labs'].get(k),k);bb=normalize(b['labs'].get(k),k)
            if aa[1] is None or bb[1] is None or not z.is_true(z.simplify(Q(aa[0])==Q(aa[1]))) or not z.is_true(z.simplify(Q(bb[0])==Q(bb[1]))):raise ValueError('exact pair required')
            av=Fraction(jnum(Q(aa[0])));bv=Fraction(jnum(Q(bb[0])))
            delta=bv-av
            if rule['direction']=='fall':delta=-delta
            if rule['measure']=='percent':
                if av==0:raise ValueError('zero baseline')
                delta=delta/av*100
            hit=delta>=Fraction(str(rule['threshold']));hits.append(hit)
            if hit:weight+=Fraction(str(rule['weight']))
            row.update(value=str(delta),triggered=hit)
        except (ValueError,KeyError,ArithmeticError) as e:unknown=True;row['reason']=str(e)
        details.append(row)
    op=config['trigger']['operator']
    # Three-valued aggregation: known decisive evidence survives unrelated missing metrics.
    if op=='any':triggered=True if any(hits) else None if unknown else False
    elif op=='all':triggered=False if False in hits else None if unknown else True
    else:
        cutoff=Fraction(str(config['trigger']['value']))
        maxweight=weight+sum(Fraction(str(r['weight'])) for r,d in zip(config['metrics'],details) if d['triggered'] is None)
        triggered=True if weight>=cutoff else False if maxweight<cutoff else None
    if 'core_min_hits' in config['trigger']:
        needed=config['trigger']['core_min_hits']
        if not isinstance(needed,int) or isinstance(needed,bool) or needed<0:raise ValueError('nonnegative core_min_hits required')
        core=[d['triggered'] for d in details if d['group']=='core']
        minimum=sum(x is True for x in core);maximum=minimum+sum(x is None for x in core)
        core_ok=True if minimum>=needed else False if maximum<needed else None
        triggered=False if triggered is False or core_ok is False else None if triggered is None or core_ok is None else True
    return {'status':'insufficient_comparable_data' if triggered is None else 'triggered' if triggered else 'not_triggered','triggered':triggered,'config':config['id']+'@'+config['version'],'identity':'research_only_not_clinically_validated','details':details,'weight':str(weight)}
