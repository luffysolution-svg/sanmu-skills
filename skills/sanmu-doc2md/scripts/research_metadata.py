#!/usr/bin/env python
"""Evidence-aware metadata for research PDFs; no OCR uploads or implicit networking."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from figure_identifiers import LABEL_PATTERN, identifier_from_match
from portable_paths import safe_path, walk_files, read_text

FIELDS = ('title', 'document_type', 'authors', 'date', 'publication_title', 'volume',
          'issue', 'pages', 'doi', 'language', 'abstract', 'zotero_item_uri', 'url', 'keywords',
          'has_supplement', 'supplement_to')
EXCLUDED = {'zotero_item_key', 'zotero_pdf_uri'}
DOI = re.compile(r'10\.\d{4,9}/[^\s<>"\u200b]+', re.I)


def is_supplemental_filename(name: str) -> bool:
    """Check if filename indicates a supplemental/supporting information file."""
    stem = Path(name).stem.lower()
    markers = ('-si', '_si', '.si', 'si.', 'supporting', 'supplement', 'appendix', 'mmc1')
    return any(m in stem for m in markers)


def detect_supplemental_pdf(main_pdf: Path, candidates: list[Path] | None = None) -> Path | None:
    """Detect candidate supplemental PDF matching main paper."""
    main_pdf = main_pdf.resolve()
    pool = candidates if candidates is not None else list(main_pdf.parent.glob('*.pdf'))
    main_stem = main_pdf.stem.lower()
    for cand in pool:
        cand_res = cand.resolve()
        if cand_res == main_pdf:
            continue
        cand_stem = cand_res.stem.lower()
        if is_supplemental_filename(cand_res.name):
            # Check if stems share common base prefix or parent
            if (cand_stem.startswith(main_stem) or main_stem.startswith(cand_stem)
                    or len(set(cand_stem.split()) & set(main_stem.split())) >= 2):
                return cand_res
    return None


def link_supplemental_references(text: str, si_doc_name: str) -> str:
    """Transform in-text references to SI figures/tables into Obsidian wikilinks.

    Converts occurrences like 'Figure S1', 'Fig. S2', 'Table S1', '附图 S1'
    into '[[<si_doc_name>#Figure S1|Figure S1]]' without altering code blocks
    or existing wikilinks.
    """
    from markdown_resources import rewrite_unprotected

    def replace(match):
        identifier = identifier_from_match(match)
        if identifier.namespace != 'supplemental':
            return match.group()
        raw = match.group()
        # The SI builder synthesizes Figure/Table/Scheme headings, but not Note
        # sections: anchoring a Note would point at a heading that never exists.
        if identifier.kind == 'Note':
            return f'[[{si_doc_name}|{raw}]]'
        return f'[[{si_doc_name}#{identifier.heading}|{raw}]]'

    return rewrite_unprotected(text, lambda segment: LABEL_PATTERN.sub(replace, segment))


def clean_document_link(value):
    """Accept a plain note path or one wikilink; forbid anchor/alias injection."""
    value = value.strip()
    if value.startswith('[[') and value.endswith(']]'):
        value = value[2:-2]
    if not value or any(c in value for c in '[]#|') or any(ord(c) < 32 for c in value):
        raise ValueError('supplement document must be a plain note name/path')
    return value.removesuffix('.md')


def ensure_supplemental_anchors(text: str) -> str:
    """Ensure every supplemental Figure/Table/Scheme mentioned in SI text has a ## heading anchor."""
    existing = {m.group(1).strip() for m in re.finditer(r'^#{2,6}\s+(.+)$', text, re.M)}
    blocks = text.split('\n\n')
    out_blocks = []
    in_fence = False
    for blk in blocks:
        stripped = blk.lstrip()
        if stripped.startswith(('```', '~~~')):
            in_fence = not in_fence
            out_blocks.append(blk)
            continue
        if in_fence or stripped.startswith(('>', '![')):
            out_blocks.append(blk)
            continue
        if stripped.startswith('# '):
            out_blocks.append(blk)
            continue
        new_headings = []
        for match in LABEL_PATTERN.finditer(blk):
            ident = identifier_from_match(match)
            if ident.namespace == 'supplemental' and ident.kind in ('Figure', 'Table', 'Scheme'):
                if ident.heading not in existing:
                    existing.add(ident.heading)
                    new_headings.append(f'## {ident.heading}')
        if new_headings:
            out_blocks.extend(new_headings)
        out_blocks.append(blk)
    return '\n\n'.join(out_blocks)


def prepare_research_markdown(text, metadata):
    """Run all research transforms in staging, before Publisher hashes bytes."""
    if metadata.get('has_supplement'):
        name = clean_document_link(metadata['has_supplement'])
        text = link_supplemental_references(text, name)
        text = text.rstrip() + ('\n\n## 补充材料 (Supporting Information)\n\n'
                               f'- 详见本文补充材料与附图：[[{name}]]\n')
    if metadata.get('supplement_to'):
        text = ensure_supplemental_anchors(text)
        text = inject_supplement_banner(text, clean_document_link(metadata['supplement_to']))
    return text


def inject_supplement_banner(si_markdown_text: str, main_doc_name: str) -> str:
    """Inject a callout banner linking back to main paper at top of SI markdown."""
    banner = (
        f'> [!info] 关联主文\n'
        f'> 本文档为 [[{main_doc_name}]] 的补充材料（Supporting Information / SI）。\n\n'
    )
    if banner in si_markdown_text:
        return si_markdown_text
    if '---' in si_markdown_text:
        parts = si_markdown_text.split('---', 2)
        if len(parts) >= 3 and parts[0].strip() == '':
            # Frontmatter exists
            return f'---{parts[1]}---\n\n{banner}{parts[2].lstrip()}'
    return banner + si_markdown_text


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with safe_path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def title_key(value: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKC', value).casefold() if c.isalnum())


def is_supplemental_title_variant(title_a: str, title_b: str) -> bool:
    """Check if title difference is solely due to supplemental/appendix markers."""
    ka = title_key(title_a)
    kb = title_key(title_b)
    markers = ('supplementalinformation', 'supplementaryinformation',
               'supplementarymaterial', 'supplementalmaterial',
               'supportinginformation', 'supplementary', 'appendix')
    for m in markers:
        if ka.replace(m, '') == kb or kb.replace(m, '') == ka:
            return True
        if ka.startswith(kb.replace(m, '')) and m in kb:
            return True
        if kb.startswith(ka.replace(m, '')) and m in ka:
            return True
    return False


def normalize_doi(value: str) -> str:
    value = urllib.parse.unquote(str(value).strip())
    value = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', value, flags=re.I)
    if not DOI.fullmatch(value):
        raise ValueError(f'Invalid DOI: {value!r}')
    return value.lower()


def _dois(text: str) -> list[str]:
    return list(dict.fromkeys(m.group().rstrip('.,;').lower() for m in DOI.finditer(text)))


def normalize_metadata(data: dict) -> dict:
    """Whitelist canonical reading fields; unknown/internal identifiers never leak."""
    if not isinstance(data, dict):
        raise ValueError('Metadata must be an object')
    result = {}
    for field in FIELDS:
        value = data.get(field)
        if value is None or value == '' or value == []:
            continue
        if field in ('authors', 'keywords'):
            if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
                raise ValueError(f'{field} must be a list of nonempty strings')
            if field == 'authors' and any(re.search(r'\bet\s+al\.?\s*$|等\s*$', v, re.I) for v in value):
                raise ValueError('Truncated author lists must be reviewed, not presented as complete')
            result[field] = [v.strip() for v in value]
        else:
            if not isinstance(value, str):
                raise ValueError(f'{field} must be a string')
            # Preserve original abstract text, including paragraph boundaries.
            result[field] = value if field == 'abstract' else value.strip()
    if 'doi' in result:
        result['doi'] = normalize_doi(result['doi'])
    if 'zotero_item_uri' in result and not re.fullmatch(
            r'zotero://select/(?:library|groups/\d+)/items/[A-Z0-9]{8}', result['zotero_item_uri']):
        raise ValueError('Invalid Zotero item URI')
    return result


def render_frontmatter(metadata: dict) -> str:
    """YAML serializer for core builders to call before ownership hash finalization."""
    import yaml
    metadata = normalize_metadata(metadata)
    return '---\n' + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + '---\n\n'


def export_identity(payload: dict) -> str | None:
    if not isinstance(payload, dict):
        return None
    record = payload.get('metadata', payload)
    if not isinstance(record, dict):
        return None
    data = record.get('data', record)
    return payload.get('id') or payload.get('item_key') or record.get('key') or (data.get('key') if isinstance(data, dict) else None)


def load_export(path: Path | None, identity: str | None, source_sha256: str) -> dict | None:
    if path is None:
        return None
    payload = json.loads(read_text(path, encoding='utf-8-sig'))
    if isinstance(payload, list):
        candidates = [p for p in payload if isinstance(p, dict) and (
            export_identity(p) == identity if identity else p.get('sha256') == source_sha256)]
        if len(candidates) != 1:
            raise ValueError('Metadata list needs --id selecting exactly one item (duplicate PDFs remain distinct)')
        payload = candidates[0]
    if not isinstance(payload, dict):
        raise ValueError('Metadata JSON must be an object or a list of exported objects')
    if payload.get('sha256') and payload['sha256'] != source_sha256:
        raise ValueError('Exported PDF SHA-256 does not match --pdf')
    if identity and export_identity(payload) and export_identity(payload) != identity:
        raise ValueError('--id and exported metadata identity differ')
    return payload


def metadata_from_export(payload: dict) -> dict:
    record = payload.get('metadata', payload)
    if not isinstance(record, dict):
        raise ValueError('Export metadata must be an object')
    data = record.get('data', record)
    if not isinstance(data, dict):
        raise ValueError('Zotero data must be an object')
    if not any(k in data for k in ('itemType', 'creators', 'abstractNote', 'publicationTitle')):
        return normalize_metadata(data)
    result = {k: data[k] for k in FIELDS if k in data and k not in ('authors', 'keywords')}
    aliases = {'abstractNote': 'abstract', 'publicationTitle': 'publication_title', 'DOI': 'doi'}
    for key, field in aliases.items():
        if data.get(key):
            result[field] = data[key]
    for field in ('volume', 'issue', 'pages'):
        if data.get(field) is not None and data.get(field) != '':
            result[field] = str(data[field])
    if data.get('itemType') == 'journalArticle':
        result['document_type'] = 'journal_article'
    authors = []
    for creator in data.get('creators', []):
        if creator.get('creatorType', 'author') != 'author':
            continue
        name = creator.get('name') or ' '.join(str(creator.get(k) or '').strip() for k in ('firstName', 'lastName')).strip()
        if name:
            authors.append(name)
    if authors:
        result['authors'] = authors
    elif data.get('authors') and isinstance(data['authors'], list):
        result['authors'] = [str(a).strip() for a in data['authors'] if str(a).strip()]
    # Zotero tags can be personal/automatic labels, not original article keywords.
    # Keep them as external provenance instead of relabeling them in frontmatter.
    # Build IDs may include an attachment suffix. Zotero selection always points
    # to the original bibliographic item, never that build/attachment identity.
    key = payload.get('item_key') or record.get('key') or data.get('key')
    library = record.get('library') or {}
    if key and re.fullmatch(r'[A-Z0-9]{8}', key):
        if library.get('type') == 'user':
            result['zotero_item_uri'] = f'zotero://select/library/items/{key}'
        elif library.get('type') == 'group' and str(library.get('id', '')).isdigit():
            result['zotero_item_uri'] = f'zotero://select/groups/{library["id"]}/items/{key}'
    return normalize_metadata(result)


def inspect_pdf(path: Path) -> dict:
    """Physical-page preflight. Text-layer presence is never claimed as text accuracy."""
    import fitz
    with fitz.open(safe_path(path)) as pdf:
        if pdf.needs_pass:
            raise ValueError('Encrypted PDF needs an unlocked user-provided copy')
        if len(pdf) == 0:
            raise ValueError('PDF has no pages')
        pages, first_text = [], ''
        for index, page in enumerate(pdf):
            text = page.get_text()
            if index == 0:
                first_text = text
            pages.append({'page': index + 1, 'text_characters': len(text.strip()),
                          'embedded_images': len(page.get_images()),
                          'width': page.rect.width, 'height': page.rect.height})
        properties = pdf.metadata or {}
        xmp = pdf.get_xml_metadata() or ''
    evidence = {'source_path': str(path.resolve()), 'sha256': sha256_file(path),
                'page_count': len(pages), 'pages': pages, 'pdf_properties': properties,
                'xmp': xmp, 'first_page_text': first_text,
                'text_layer': 'present_unverified' if any(p['text_characters'] for p in pages) else 'absent',
                'review': [], 'candidates': {}, 'fields': {}}
    fields = evidence['fields']
    key = title_key(first_text)
    title = properties.get('title', '').strip()
    # XMP title is still only a clue unless present in the current PDF text.
    if not title and xmp:
        try:
            tree = ET.fromstring(xmp)
            ns = {'dc': 'http://purl.org/dc/elements/1.1/'}
            node = tree.find('.//dc:title', ns)
            if node is not None:
                title = ''.join(node.itertext()).strip()
        except ET.ParseError:
            evidence['review'].append('Malformed XMP; retained as raw evidence')
    if title:
        evidence['candidates']['title'] = title
        if title_key(title) and title_key(title) in key:
            fields['title'] = title
            if len(title_key(title)) < 12:
                evidence['review'].append('Short PDF title matches first-page text; identity evidence is weak and needs review')
        else:
            evidence['review'].append('PDF property/XMP title not corroborated on first physical page')
    # Stop before an explicitly marked references section on the first page.
    header = re.split(r'(?im)^\s*(?:references|bibliography|参考文献)\s*$', first_text)[0]
    page_dois = _dois(header)
    property_dois = _dois(' '.join(str(properties.get(k) or '') for k in ('subject', 'keywords')))
    evidence['candidates']['first_page_dois'] = page_dois
    evidence['candidates']['property_dois'] = property_dois
    corroborated = set(page_dois) & set(property_dois)
    if fields.get('title') and len(corroborated) == 1:
        fields['doi'] = corroborated.pop()
    elif page_dois:
        evidence['review'].append('DOI candidates require identity review; first-page occurrence alone is insufficient')
    if properties.get('author'):
        evidence['candidates']['author_property'] = properties['author']
    evidence['review'].append('PDF author properties are not a verified full author list; no automatic author parsing')
    return evidence


def _crossref_metadata(message: dict) -> dict:
    result = {}
    for key, field in [('title', 'title'), ('container-title', 'publication_title')]:
        if message.get(key):
            result[field] = message[key][0]
    for key, field in [('DOI', 'doi'), ('volume', 'volume'), ('issue', 'issue'), ('page', 'pages'), ('URL', 'url')]:
        if message.get(key):
            result[field] = str(message[key])
    authors = []
    for author in message.get('author', []):
        name = author.get('name') or ' '.join(str(author.get(k) or '') for k in ('given', 'family')).strip()
        if name:
            authors.append(name)
    if authors:
        result['authors'] = authors
    if message.get('abstract'):
        try:
            result['abstract'] = ''.join(ET.fromstring('<root>' + message['abstract'] + '</root>').itertext())
        except ET.ParseError:
            # JATS with unresolved namespaces is not silently passed off as plain abstract.
            pass
    # Do not pick issued/online/print dates interchangeably for an unknown PDF version.
    return normalize_metadata(result)


def match_crossref(message: dict, doi: str, title: str) -> dict:
    """Require both exact normalized title and DOI. No fuzzy/title-search fallback."""
    titles = message.get('title') or []
    if not titles or title_key(titles[0]) != title_key(title) or len(title_key(title)) < 12:
        raise ValueError('Crossref title mismatch; metadata not applied')
    if normalize_doi(message.get('DOI', '')) != normalize_doi(doi):
        raise ValueError('Crossref DOI mismatch; metadata not applied')
    return _crossref_metadata(message)


def lookup_crossref(doi: str, title: str, cache_dir: Path) -> tuple[dict, dict]:
    """Explicit opt-in DOI-only lookup with persistent raw-response cache and timeout."""
    doi = normalize_doi(doi)
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / (hashlib.sha256(doi.encode()).hexdigest() + '.json')
    cached = target.is_file()
    if cached:
        message = json.loads(target.read_text(encoding='utf-8'))
    else:
        request = urllib.request.Request('https://api.crossref.org/works/' + urllib.parse.quote(doi, safe=''),
                                         headers={'User-Agent': 'sanmu-doc2md/1.0 (metadata lookup)',
                                                  'Accept': 'application/json'})
        with urllib.request.urlopen(request, timeout=15) as response:
            message = json.loads(response.read())['message']
        # Only cache records that pass the gate; cache hits run the gate again.
        match_crossref(message, doi, title)
        target.write_text(json.dumps(message, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return match_crossref(message, doi, title), {'service': 'Crossref', 'doi': doi, 'cache_hit': cached,
                                               'match': 'exact_normalized_title_and_doi', 'record': message}


def collect_metadata(preflight: dict, payload: dict | None = None,
                     *, crossref: bool = False, cache_dir: Path | None = None) -> tuple[dict, dict]:
    fields = dict(preflight['fields'])
    provenance = {'field_sources': {k: 'pdf_properties_corroborated_first_page' for k in fields},
                  'review': list(preflight['review']), 'conflicts': []}
    if payload is not None:
        supplied = metadata_from_export(payload)
        raw_record = payload.get('metadata', payload)
        raw_data = raw_record.get('data', raw_record)
        if raw_data.get('tags'):
            provenance['source_tags'] = raw_data['tags']
        if payload.get('sha256') and payload['sha256'] != preflight['sha256']:
            raise ValueError('Exported PDF SHA-256 mismatch')
        if fields.get('doi') and supplied.get('doi') and fields['doi'] != supplied['doi']:
            raise ValueError('Export DOI and current PDF DOI differ; review version/source association')
        if fields.get('title') and supplied.get('title') and title_key(fields['title']) != title_key(supplied['title']):
            if is_supplemental_title_variant(fields['title'], supplied['title']):
                provenance['review'].append('Supplied title identified as supplemental/supporting variant of PDF title')
            else:
                raise ValueError('Export title and current PDF title differ; review version/source association')
        first_page_key = title_key(preflight['first_page_text'])
        supplied_title_key = title_key(supplied.get('title', ''))
        title_match = bool(supplied_title_key and (
            supplied_title_key in first_page_key or supplied_title_key == title_key(fields.get('title', ''))
            or is_supplemental_title_variant(fields.get('title', ''), supplied.get('title', ''))
            or (fields.get('title') and title_key(fields['title']) in first_page_key and any(
                m in first_page_key for m in ('supplemental', 'supplementary', 'supporting', 'appendix')))))
        if title_match and len(supplied_title_key) < 12:
            provenance['review'].append('Short supplied title matches first-page/PDF title; identity evidence is weak and needs review')
        # A matching attachment hash establishes bytes, not bibliographic accuracy.
        # Refuse unrelated supplied titles even if the export also carries that hash.
        if supplied_title_key and len(first_page_key) >= 32 and not title_match:
            raise ValueError('Supplied title not corroborated by current PDF first page; review metadata association')
        page_dois = preflight.get('candidates', {}).get('first_page_dois', [])
        if supplied.get('doi') and page_dois and supplied['doi'] not in page_dois:
            # A first-page DOI may cite another paper. Only the independently
            # corroborated fields['doi'] above is strong enough to reject input.
            provenance['conflicts'].append({'field': 'doi', 'kept': supplied['doi'],
                                             'first_page_candidates': page_dois,
                                             'status': 'unconfirmed_candidate_conflict'})
            provenance['review'].append('Supplied DOI differs from unconfirmed first-page DOI candidates; candidates may be body citations and need source review')
        matched = title_match
        if payload.get('sha256') == preflight['sha256'] and not title_match:
            provenance['review'].append('PDF hash confirms attachment bytes only; bibliographic identity remains unverified')
        if not matched:
            provenance['review'].append('Supplied metadata association not corroborated by PDF title; user review required')
            # A Zotero key without corroborated PDF association is not a reliable link.
            supplied.pop('zotero_item_uri', None)
        fields.update(supplied)
        provenance['field_sources'].update({k: 'supplied_export' for k in supplied})
        provenance['export_identity'] = export_identity(payload)
        provenance['export_pdf_association'] = 'corroborated' if matched else 'needs_review'
    if crossref:
        if not fields.get('doi') or not fields.get('title'):
            provenance['review'].append('Crossref skipped: confirmed DOI and title are required')
        else:
            try:
                extra, record = lookup_crossref(fields['doi'], fields['title'], cache_dir or Path('.crossref-cache'))
                provenance['crossref'] = record
                for key, value in extra.items():
                    if key in fields and fields[key] != value:
                        provenance['conflicts'].append({'field': key, 'kept': fields[key], 'crossref': value})
                    elif key not in fields:
                        fields[key] = value
                        provenance['field_sources'][key] = 'crossref_exact_title_doi'
                provenance['review'].append('Crossref title/DOI match does not establish PDF version or author-list completeness; verify against source')
            except (OSError, ValueError, KeyError, TypeError) as exc:
                provenance['review'].append(f'Crossref not applied: {exc}')
    fields = normalize_metadata(fields)
    provenance['missing_fields'] = [key for key in ('title', 'authors', 'abstract') if key not in fields]
    provenance['review'].extend(f'Missing {key}: needs source review' for key in provenance['missing_fields'])
    return fields, provenance


def sanitize_filename_title(title: str, max_length: int = 100) -> str:
    """Sanitize title for filesystem safety following Zotero conventions."""
    t = str(title).replace(':', ' - ')
    t = re.sub(r'[\\/*?\"<>|]', '_', t)
    t = re.sub(r'\s+', ' ', t).strip()
    if len(t) > max_length:
        t = t[:max_length].rstrip(' .-_')
    return t


def format_zotero_basename(metadata: dict | None = None, fallback_stem: str = '',
                           extra_candidates: dict | None = None) -> str:
    """Format file/folder basename following Zotero pattern:
    {{ firstCreator suffix=" - " }}{{ year suffix=" - " }}{{ title truncate="100" }}
    Supports canonical metadata dict, Zotero payload/item/data wrapper, independent PDF/Word candidates, or empty dict.
    """
    first_creator = ''
    year = ''
    raw_title = ''

    if isinstance(metadata, dict):
        data = metadata.get('metadata', metadata)
        if isinstance(data, dict):
            data = data.get('data', data)
        if not isinstance(data, dict):
            data = metadata

        creators = ([c for c in data.get('creators', []) if isinstance(c, dict)
                     and c.get('creatorType', 'author') == 'author']
                    or data.get('authors') or metadata.get('authors') or [])
        if creators and isinstance(creators, list):
            first = creators[0]
            multi = len(creators) > 1
            if isinstance(first, dict):
                name = first.get('lastName') or first.get('name') or first.get('firstName') or ''
            elif isinstance(first, str):
                s = first.strip()
                if ',' in s:
                    name = s.split(',')[0].strip()
                elif re.search(r'[\u4e00-\u9fff]', s):
                    name = s
                else:
                    parts = s.split()
                    name = parts[-1] if parts else s
            else:
                name = str(first).strip()
            name = name.strip()
            if name:
                first_creator = f'{name} 等' if multi else name

        date_str = str(data.get('date') or metadata.get('date') or '')
        m_year = re.search(r'(?:19|20)\d{2}', date_str)
        if m_year:
            year = m_year.group(0)

        raw_title = data.get('title') or metadata.get('title') or ''

    # Unverified document properties must never supply publication facts.
    # The original filename is an opaque display fallback only.
    if not raw_title:
        raw_title = fallback_stem.strip()

    title = sanitize_filename_title(raw_title, max_length=100) if raw_title else ''
    parts = [p for p in (first_creator, year, title) if p]
    if not parts:
        return sanitize_filename_title(fallback_stem or 'document', max_length=100)
    return sanitize_filename_title(' - '.join(parts), max_length=180)
