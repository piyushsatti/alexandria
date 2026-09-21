// Pure data contracts. No filesystem, network, model calls, or acceptance authority.
import crypto from 'node:crypto';

export const hash = value => crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex');
const check = (ok, message) => { if (!ok) throw Error(message); };
const keys = (row, expected) => check(row && typeof row === 'object' && !Array.isArray(row) && Object.keys(row).sort().join('|') === [...expected].sort().join('|'), 'Unexpected or missing fields');
const ids = rows => {
  check(Array.isArray(rows), 'Expected list');
  const result = new Map();
  for (const row of rows) {
    check(typeof row.id === 'string' && /^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(row.id) && !result.has(row.id), 'Invalid or duplicate ID');
    result.set(row.id, row);
  }
  return result;
};
const markers = text => [...text.matchAll(/\[AMBIGUOUS: ([A-Za-z][A-Za-z0-9_-]*)\]/g)].map(m => m[1]).sort();

// Every nonempty source line has a region. Not an automatic semantic decomposition.
// Preserve contiguous Markdown blocks (including whole fences), with exact offsets.
export function inventory(docs) {
  const regions = [];
  for (const [path, text] of Object.entries(docs).sort()) {
    let offset = 0, start = null, fence = null;
    const flush = () => {
      if (start !== null) {
        const quote = text.slice(start, offset);
        regions.push({offset_unit:'utf16-code-unit', id: 'r_' + hash([path, start, quote]).slice(0, 20), path, start, end: offset, quote, line: text.slice(0, start).split('\n').length});
        start = null;
      }
    };
    for (const line of text.match(/[^\n]*\n|[^\n]+$/g) || []) {
      const token = line.match(/^ {0,3}(`{3,}|~{3,})/);
      if (!line.trim() && !fence) { flush(); offset += line.length; continue; }
      if (start === null) start = offset;
      offset += line.length;
      if (token) {
        if (!fence) fence = token[1];
        else if (token[1][0] === fence[0] && token[1].length >= fence.length) fence = null;
      }
    }
    flush();
  }
  return regions;
}

function uniqueQuote(row, docs) {
  check(typeof row.quote === 'string' && row.quote.trim() && Object.hasOwn(docs, row.path), 'Invalid quote source');
  const first = docs[row.path].indexOf(row.quote);
  check(first >= 0 && docs[row.path].indexOf(row.quote, first + 1) < 0, 'Quote missing or nonunique');
}

export function validateCandidate(data, docs, regions) {
  const claims = ids(data.claims), annotations = ids(data.annotations);
  check([...claims.keys()].every(id => !annotations.has(id)), 'Claim/annotation ID collision');
  for (const row of [...claims.values(), ...annotations.values()]) uniqueQuote(row, docs);
  for (const claim of claims.values()) {
    for (const field of ['subject','predicate','object']) check(typeof claim[field] === 'string' && claim[field].trim(), 'Empty relationship');
    check(['accepted','proposed','open','requirement','limitation','historical','deferred'].includes(claim.source_status), 'Invalid source status');
    if (claim.review_state !== undefined) check(['not_checked','held'].includes(claim.review_state), 'Invalid review state');
    uniqueQuote(claim.scope, docs);
    check(typeof claim.scope.text === 'string' && claim.scope.text.trim(), 'Missing standalone scope');
    check(Array.isArray(claim.annotation_ids) && new Set(claim.annotation_ids).size === claim.annotation_ids.length && claim.annotation_ids.every(id => annotations.has(id)), 'Invalid claim annotation');
  }
  for (const a of annotations.values()) check(['ambiguity','open_decision','scope_tension','interpretation'].includes(a.kind), 'Invalid annotation kind');
  const coverage = ids(data.coverage), regionMap = new Map(regions.map(r => [r.id, r]));
  check(coverage.size === regionMap.size && [...coverage.keys()].every(id => regionMap.has(id)), 'Missing or invented source region');
  const mapped = new Set();
  for (const row of coverage.values()) {
    keys(row, ['id','disposition','claim_ids','annotation_ids','reason']);
    check(['represented','excluded','unresolved','not_checked'].includes(row.disposition), 'Invalid coverage disposition');
    check(typeof row.reason === 'string' && row.reason.trim(), 'Coverage reason required');
    for (const [field, map] of [['claim_ids',claims],['annotation_ids',annotations]]) {
      check(Array.isArray(row[field]) && row[field].every(id => map.has(id)) && new Set(row[field]).size === row[field].length, 'Invalid coverage references');
      for (const id of row[field]) {
        const target = map.get(id), region = regionMap.get(row.id);
        const evidence = field === 'claim_ids' ? [target,target.scope] : [target];
        check(evidence.some(span => {
          const start = docs[span.path].indexOf(span.quote);
          return span.path === region.path && start < region.end && start + span.quote.length > region.start;
        }), 'Coverage reference does not overlap claim or scope evidence');
        if (field === 'claim_ids') mapped.add(id);
      }
    }
    if (row.disposition === 'represented') check(row.claim_ids.length + row.annotation_ids.length > 0, 'Represented region has no evidence');
    if (row.disposition === 'excluded') check(!row.claim_ids.length && !row.annotation_ids.length, 'Excluded region has output references');
  }
  check([...claims.keys()].every(id => mapped.has(id)), 'Claim not accounted for in source ledger');
  const used = new Set();
  check(Array.isArray(data.pages), 'Missing pages');
  for (const page of data.pages) for (const section of page.sections) for (const p of section.paragraphs) {
    check(typeof p.text === 'string' && p.text.trim(), 'Empty paragraph');
    check(Array.isArray(p.claim_ids) && Array.isArray(p.annotation_ids) && p.claim_ids.length + p.annotation_ids.length, 'Unattributed paragraph');
    check(p.claim_ids.every(id => claims.has(id)) && p.annotation_ids.every(id => annotations.has(id)), 'Unknown paragraph reference');
    for (const id of p.claim_ids) used.add(id);
    for (const id of markers(p.text)) check(p.annotation_ids.includes(id), 'Unbound ambiguity marker');
    for (const id of p.annotation_ids) if (['ambiguity','scope_tension'].includes(annotations.get(id).kind)) check(markers(p.text).includes(id), 'Missing ambiguity marker');
  }
  check([...claims.keys()].every(id => used.has(id)), 'Claim absent from reader pages');
  return {claims: claims.size, regions: regions.length};
}

// Trusted code owns paragraph identity and reference mapping for this candidate.
export function freezeParagraphs(data) {
  const frozen = structuredClone(data);
  let number = 0;
  for (const page of frozen.pages) for (const section of page.sections) for (const p of section.paragraphs) {
    p.id = 'p_' + (++number) + '_' + hash(p).slice(0, 12);
  }
  return frozen;
}
export function editableParagraphs(data) {
  return data.pages.flatMap(page => page.sections.flatMap(s => s.paragraphs.map(p => ({id:p.id, text:p.text}))));
}
export function applyEdits(frozen, response) {
  keys(response, ['edits']);
  const edits = ids(response.edits), originals = editableParagraphs(frozen);
  check(edits.size === originals.length && originals.every(p => edits.has(p.id)), 'Missing or invented paragraph');
  for (const p of originals) {
    const edit = edits.get(p.id);
    keys(edit, ['id','text']);
    check(typeof edit.text === 'string' && edit.text.trim(), 'Empty edited paragraph');
    check(JSON.stringify(markers(edit.text)) === JSON.stringify(markers(p.text)), 'Moved or missing ambiguity marker');
  }
  const result = structuredClone(frozen);
  for (const page of result.pages) for (const section of page.sections) for (const p of section.paragraphs) p.text = edits.get(p.id).text;
  return result;
}

export function reviewQueue(data, regions) {
  const queue = [];
  const patterns = [
    ['conditions-negation', /\b(only|unless|except|must|may|shall|not|never|cannot|before|after|if|require|requires|required)\b/i],
    ['authority-privacy', /\b(publish|publication|release|delete|deletion|restore|rollback|privacy|permission|approval)\b/i],
    ['time-status', /\b(current|now|historical|proposed|deferred|pending|open|superseded)\b|\b20\d\d[.-]\d\d[.-]\d\d\b/i],
    ['table-diagram', /\||```|~~~|-->/],
  ];
  for (const region of regions) {
    const flags = patterns.filter(([, re]) => re.test(region.quote)).map(([name]) => name);
    queue.push({id:'source_' + region.id, target:region.id, type:'source-region', flags, review_state:'not_checked', source: {path:region.path,start:region.start,end:region.end,offset_unit:region.offset_unit}});
  }
  const names = new Map();
  for (const c of data.claims) for (const label of [c.subject,c.object]) {
    const key = JSON.stringify([c.path,label]);
    if (!names.has(key)) names.set(key, []);
    names.get(key).push(c);
  }
  for (const [key, rows] of names) {
    const unique = [...new Map(rows.map(c => [c.id,c])).values()];
    if (new Set(unique.map(c => c.quote)).size > 1) queue.push({id:'identity_' + hash(key).slice(0,20),type:'repeated-label',target:JSON.parse(key),claim_ids:unique.map(c=>c.id),flags:['identity-not-proven'],review_state:'not_checked'});
  }
  return queue;
}

export function holdReceipt(data, regions, review) {
  const issueFields = ['unsupported_additions','lost_information','status_errors','ambiguity_marker_errors','atomicity_errors','standalone_scope_errors'];
  check(review && typeof review.sensible === 'boolean' && issueFields.every(k => Array.isArray(review[k]) && review[k].every(x => typeof x === 'string')), 'Incomplete meaning review');
  return {
    status:'held-pending-owner-review', automatic_acceptance:false,
    candidate_hash:hash(data), coverage_hash:hash(data.coverage), source_inventory_hash:hash(regions),
    model_review_pass: review.sensible && issueFields.every(k => !review[k].length),
    empty_extraction: !data.claims.length,
    coverage_proposals:Object.fromEntries(['represented','excluded','unresolved','not_checked'].map(s=>[s,data.coverage.filter(r=>r.disposition===s).length])),
    review_required: 'All dispositions are model proposals. Check source omissions, exclusions, sensitive regions and meaning before any owner decision. No flags does not establish safety.'
  };
}
