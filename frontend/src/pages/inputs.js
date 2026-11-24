import {feedback, field} from '../components/common/fields.js';
import {empty, panel} from '../components/common/panels.js';
import {table} from '../components/common/table.js';
import {handler} from '../hooks/action.js';
import {session} from '../stores/session.js';
import {workspace} from '../stores/workspace.js';
import {button, el, link, p} from '../utils/dom.js';
import {date} from '../utils/format.js';

const KIND_LABEL = {checkout: '随机结账台账', finance: '财务退款日账'};

function kindFromHash(route) {
  return route.kind === 'finance' ? 'finance' : 'checkout';
}

function specFor(kind) {
  return kind === 'finance'
    ? {fields: workspace.spec.finance_fields, sample: workspace.spec.finance_sample,
       help: workspace.spec.finance_help}
    : {fields: workspace.spec.fields, sample: workspace.spec.sample_name, help: null};
}

export async function inputs(route, refresh) {
  const kind = kindFromHash(route);
  const collection = kind === 'finance' ? workspace.financeDatasets : workspace.datasets;
  const config = specFor(kind);
  const selected = collection.find(item => item.id === Number(route.id)) || collection[0];
  const error = feedback();
  const sample = button('下载示例 CSV', {class:'quiet',onClick:event =>
    handler(sample,error,() => workspace.download(`/template?kind=${kind}`, config.sample))(event)});
  const detail = selected && await workspace.getDataset(selected.id, route.offset);
  const columns = config.fields.map(item => ({key:item.key, label:item.label}));
  const preview = detail ? panel(KIND_LABEL[kind], el('div',{class:'actions'},
    el('label',{for:'dataset-id',class:'sr'}, '选择数据批次'),
    el('select',{id:'dataset-id','aria-label':'选择数据批次',onChange:event => {
      location.hash = `#inputs/${kind}/${event.target.value}`;}},
      collection.map(item => el('option',{value:item.id, selected:item.id === selected.id}, item.name))),
    kind === 'checkout' && link('使用此批次运行', `#run/${detail.id}`, {class:'quiet'}),
    kind === 'finance' && link('在运行页挂接此财务日账', '#run', {class:'quiet'})),
    p(`${detail.filename} · ${detail.row_count} 行 · ${date(detail.created_at)}`, 'filename'),
    table(columns, detail.rows, {caption:'批次明细', pageSize:16}),
    el('div',{class:'pagination'},
      route.offset > 0 && link('上一批明细', `#inputs/${kind}/${detail.id}/${Math.max(0,route.offset-16)}`, {class:'quiet'}),
      p(`${detail.rows.length ? route.offset+1 : route.offset}–${route.offset+detail.rows.length} / ${detail.row_count}`),
      route.offset+16 < detail.row_count && link('下一批明细', `#inputs/${kind}/${detail.id}/${route.offset+16}`, {class:'quiet'})))
    : empty(`没有可用的${KIND_LABEL[kind]}批次。请导入示例或业务 CSV。`, '#run', '前往运行');
  const submit = button('校验并导入', {type:'submit', class:'primary'});
  const upload = session.canWrite && el('form',{class:'fields',onSubmit:handler(submit,error,async event => {
    const data = new FormData(event.target);
    const file = data.get('source');
    if (!file?.size) throw new Error('请选择一个 CSV 文件');
    if (file.size > 2_000_000) throw new Error('CSV 最多 2 MB');
    const item = await workspace.upload({
      name:data.get('batch_name'), filename:file.name, csv_text:await file.text(), kind,
    });
    const target = `#inputs/${kind}/${item.id}`;
    if (location.hash === target) await refresh();
    else location.hash = target;
  })}, field({key:'batch_name', label:'批次名称', type:'string', max_length:120}),
    el('label',{class:'field',for:'source'}, el('span',{},'业务 CSV'),
      el('input',{id:'source',type:'file',name:'source',accept:'.csv,text/csv',required:true})), submit);
  const switcher = el('div', {class:'kind-switch'},
    el('a', {href:'#inputs', class:kind === 'checkout' ? 'active' : ''}, '随机结账台账'),
    el('a', {href:'#inputs/finance', class:kind === 'finance' ? 'active' : ''}, '财务退款日账'));
  return el('div',{class:'two-col'},el('div',{class:'wide-panel'},switcher,preview),
    el('aside',{class:'sidebar-panel'},
      panel('数据导入', p('导入后保留原批次和校验指纹，可用同一份数据复跑；运行发起时锁定批次，并发补报只生成新批次。','subtle'),
        config.help && p(config.help, 'subtle'), sample, upload, error),
      panel('字段说明', el('dl',{class:'dictionary'}, config.fields.map(item =>
        el('div',{},el('dt',{},item.label),
          el('dd',{},item.key, item.required === false ? ' · 可空' : ' · 必填',
            item.options ? ` · ${item.options.join(' / ')}` : '')))))));
}
