/* Boulangerie: browse a croissant's files (in their folders) and tables (by kind).
 *
 * Boulangerie.files(root, opts) renders the folder tree with search and a "needs a look" filter. It loads
 * files-tree.json (the tree plus one short row per file) up front, and each file's full details only when that
 * file is opened, from files/<chunk>.json, so even PGS Catalog's 17,000 files load quickly.
 *
 * Boulangerie.tables(root, opts) renders the kinds of tables (one set of columns each) from tables.json.
 *
 * All text from croissants goes into the page with textContent, never as HTML.
 */
(function () {
  var PAGE = 50, CHUNK = 100, FILE_LIST = 20;
  var SEV = { problem: 'Needs fixing', warning: 'Worth checking', note: 'Nice to have' };
  var ICON = { folder: '📁', archive: '🗜️', group: '🗂️', family: '🗂️',
               formats: '📑', fileset: '🗃️', file: '📄' };
  var WHY_FLAGGED = 'Why we flagged this: the pipeline that wrote your croissant wasn’t fully sure about it.';

  // ── Small DOM helpers ────────────────────────────────────────────────────
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  function link(href, text) { var a = el('a', null, text); a.href = href; return a; }
  function size(n) {
    if (!n) return '';
    var units = ['bytes', 'KB', 'MB', 'GB', 'TB'], i = 0;
    while (n >= 1000 && i < units.length - 1) { n /= 1000; i++; }
    return i === 0 ? n + ' bytes' : n.toFixed(1) + ' ' + units[i];
  }
  var PLURALS = { 'kind of table': 'kinds of tables' };
  function plural(n, word) { return n.toLocaleString() + ' ' + (n === 1 ? word : (PLURALS[word] || word + 's')); }
  function lookBadge(n) {
    return n ? el('span', 'badge badge-warn', n === true ? 'Needs a look' : n.toLocaleString() + ' to look at') : null;
  }
  function reportLink(form, what, label) {
    var a = link(form + '&item=' + encodeURIComponent(what), label || 'Something wrong with this? Tell us →');
    a.className = 'text-link small';
    var p = el('p'); p.appendChild(a); return p;
  }
  // "a › b › name": folders muted, last part bold.
  function pathLabel(path) {
    var span = el('span', 'path');
    var parts = String(path).split(' › ');
    if (parts.length > 1) span.appendChild(el('span', 'path-folders', parts.slice(0, -1).join(' › ') + ' › '));
    span.appendChild(el('strong', null, parts[parts.length - 1]));
    return span;
  }
  function chips(columns) {
    var box = el('div', 'columns');
    columns.forEach(function (c) {
      var chip = el('span', 'col');
      chip.appendChild(el('code', null, c[0]));
      if (c[1]) chip.appendChild(el('span', 'col-type', c[1]));
      box.appendChild(chip);
    });
    return box;
  }
  // A long list rendered a chunk at a time.
  function chunked(container, items, render, chunk) {
    var shown = 0;
    var more = el('button', 'more', '');
    more.type = 'button';
    function next() {
      items.slice(shown, shown + chunk).forEach(function (it) { container.appendChild(render(it)); });
      shown = Math.min(items.length, shown + chunk);
      if (shown < items.length) {
        more.textContent = 'Show ' + Math.min(chunk, items.length - shown).toLocaleString() + ' more of ' +
          (items.length - shown).toLocaleString();
        container.appendChild(more);
      } else if (more.parentNode) more.parentNode.removeChild(more);
    }
    more.addEventListener('click', function () { more.parentNode.removeChild(more); next(); });
    next();
  }
  function getJSON(url) {
    return fetch(url).then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status + ' for ' + url); return r.json(); });
  }
  // Fill a <details> the first time it's opened.
  function onFirstOpen(details, fill) {
    details.addEventListener('toggle', function () {
      if (details.open && !details._filled) { details._filled = true; fill(); }
    });
  }
  function issuesAndDoubts(body, issues, unsure) {
    if (issues.length) {
      var ul = el('ul', 'item-issues');
      issues.forEach(function (i) {
        var li = el('li');
        li.appendChild(el('span', 'sev sev-' + i.severity, SEV[i.severity]));
        li.appendChild(document.createTextNode(' ' + i.title + (i.count > 1 ? ' (' + i.count.toLocaleString() + ' tables)' : '')));
        ul.appendChild(li);
      });
      body.appendChild(ul);
    }
    if (unsure && unsure.length) {
      body.appendChild(el('p', 'small', 'Parts we weren’t sure about:'));
      var u = el('ul', 'unsure');
      unsure.forEach(function (x) {
        var li = el('li');
        li.appendChild(el('strong', null, x.part));
        li.appendChild(document.createTextNode(' = ' + x.value));
        u.appendChild(li);
      });
      body.appendChild(u);
    }
  }
  // Search box, "needs a look" checkbox, status line, view and pager.
  function controls(root, placeholder) {
    var c = {};
    var bar = el('div', 'controls');
    var search = el('label', 'search');
    search.appendChild(el('span', 'visually-hidden', 'Search'));
    c.q = el('input'); c.q.type = 'search'; c.q.placeholder = placeholder; c.q.autocomplete = 'off';
    search.appendChild(c.q);
    bar.appendChild(search);
    var check = el('label', 'check');
    c.look = el('input'); c.look.type = 'checkbox';
    c.lookLabel = el('span');
    check.appendChild(c.look); check.appendChild(document.createTextNode(' ')); check.appendChild(c.lookLabel);
    bar.appendChild(check);
    root.appendChild(bar);
    c.status = el('p', 'muted small'); c.status.setAttribute('aria-live', 'polite'); c.status.textContent = 'Loading…';
    root.appendChild(c.status);
    c.view = el('div');
    root.appendChild(c.view);
    c.pager = el('nav', 'pager'); c.pager.hidden = true;
    c.prev = el('button', 'button button-quiet', '← Previous'); c.prev.type = 'button';
    c.next = el('button', 'button button-quiet', 'Next →'); c.next.type = 'button';
    c.pager.appendChild(c.prev); c.pager.appendChild(c.next);
    root.appendChild(c.pager);
    return c;
  }
  function pagedList(c, state, items, card, noun) {
    var pages = Math.max(1, Math.ceil(items.length / PAGE));
    state.page = Math.min(state.page, pages - 1);
    var start = state.page * PAGE, shown = items.slice(start, start + PAGE);
    var ul = el('ul', 'items');
    shown.forEach(function (it) { ul.appendChild(card(it)); });
    c.view.appendChild(ul);
    c.status.textContent = items.length === 0 ? 'Nothing matches.' :
      'Showing ' + (start + 1).toLocaleString() + '–' + (start + shown.length).toLocaleString() + ' of ' +
      plural(items.length, noun) + '.';
    c.pager.hidden = pages <= 1;
    c.prev.disabled = state.page === 0;
    c.next.disabled = state.page >= pages - 1;
  }

  // ── What we know about one file ──────────────────────────────────────────
  var PROPERTY_LABELS = {
    '@type': 'Croissant type', '@id': 'Identifier', name: 'Name', description: 'Description',
    contentUrl: 'Download link', encodingFormat: 'Kind of file', contentSize: 'Size',
    sha256: 'Checksum (SHA-256)', md5: 'Checksum (MD5)', containedIn: 'Inside', includes: 'Files matching',
    excludes: 'Except files matching', sameAs: 'Same content as', dateModified: 'Last modified'
  };
  var PROPERTY_ORDER = ['name', 'description', 'encodingFormat', 'contentSize', 'contentUrl', 'includes', 'excludes',
                        'containedIn', 'dateModified', 'sha256', 'md5', 'sameAs', '@type', '@id'];
  function facts(rows) {
    var dl = el('dl', 'facts');
    rows.forEach(function (r) {
      var dt = el('dt');
      dt.appendChild(document.createTextNode(r[0]));
      if (r[2]) { dt.appendChild(document.createTextNode(' ')); dt.appendChild(el('code', 'prop', r[2])); }
      dl.appendChild(dt);
      var dd = el('dd');
      if (typeof r[1] === 'string') dd.textContent = r[1]; else dd.appendChild(r[1]);
      dl.appendChild(dd);
    });
    return dl;
  }
  function propertyValue(key, value, f) {
    if (Array.isArray(value)) {
      var span = el('span');
      value.forEach(function (v, k) { if (k) span.appendChild(document.createTextNode(', ')); span.appendChild(propertyValue(key, v, f)); });
      return span;
    }
    if (value && typeof value === 'object') {
      if (key === 'containedIn' && value['@id']) return document.createTextNode(f.inside || value['@id']);
      return el('code', null, JSON.stringify(value));
    }
    var text = String(value);
    if (/^(https?|ftp):\/\//.test(text)) return link(text, text);
    if (key === 'encodingFormat' && f.kind && f.kind !== text) return document.createTextNode(text + ' (' + f.kind + ')');
    if (key === 'contentSize' && f.size && f.size !== text) return document.createTextNode(text + ' (' + f.size + ')');
    if (key === 'sha256' || key === 'md5') return el('code', 'checksum', text);
    return document.createTextNode(text);
  }
  function fileDetails(f, opts) {
    var body = el('div', 'item-body');
    body.appendChild(el('h4', 'facts-heading', 'What the croissant says about this file'));
    var keys = Object.keys(f.entry).sort(function (a, b) {
      var ia = PROPERTY_ORDER.indexOf(a), ib = PROPERTY_ORDER.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
    var rows = keys.map(function (k) { return [PROPERTY_LABELS[k] || k, propertyValue(k, f.entry[k], f), k]; });
    if (!f.entry.description) {
      rows.splice(1, 0, ['Description', el('em', 'muted', 'none. If you can tell us what this file is, please use the link below.'), 'description']);
    }
    body.appendChild(facts(rows));

    body.appendChild(el('h4', 'facts-heading', 'What we found'));
    var found = [];
    if (f.link_check) found.push(['Link check', f.link_check]);
    if (f.table_kind_names && f.table_kind_names.length) {
      var kinds = el('span');
      f.table_kind_names.forEach(function (name, n) {
        if (n) kinds.appendChild(document.createTextNode(', '));
        kinds.appendChild(link(opts.tablesUrl + '?q=' + encodeURIComponent(name), name));
      });
      found.push(['Tables in it', kinds]);
    } else {
      found.push(['Tables in it', f.empty ? 'None (the file is empty)'
                                : f.tabular ? 'None described yet. This file holds a table, so we should list its columns.'
                                            : 'None (it isn’t a table)']);
    }
    body.appendChild(facts(found));
    if (f.look && !f.issues.length) body.appendChild(el('p', 'why', WHY_FLAGGED));
    issuesAndDoubts(body, f.issues, f.unsure);
    body.appendChild(reportLink(opts.fileForm, f.path));
    return body;
  }

  // ── Files: the folder tree, with search ──────────────────────────────────
  // files-tree.json rows: [path, kind, size, needs a look (0/1), rank]
  var PATH = 0, KIND = 1, SIZE = 2, LOOK = 3, RANK = 4;

  function files(root, opts) {
    var c = controls(root, 'Search file names and folders…');
    var state = { q: '', lookOnly: false, page: 0 };
    var data = null, chunks = {};

    function details(i) {
      var k = Math.floor(i / data.chunk);
      if (!chunks[k]) chunks[k] = getJSON(opts.detailsBase + k + '.json');
      return chunks[k].then(function (list) { return list[i - k * data.chunk]; });
    }
    function fillDetails(container, i) {
      var wait = el('p', 'muted small', 'Loading…');
      container.appendChild(wait);
      details(i).then(function (f) { container.replaceChild(fileDetails(f, opts), wait); })
        .catch(function (e) { wait.textContent = 'Sorry, the details didn’t load (' + e.message + ').'; });
    }
    function nodeMeta(n) {
      var bits = [];
      if (n.type === 'group') bits.push(plural(n.count, n.of === 'files' ? 'file' : 'folder') + ' named like this');
      else if (n.type === 'formats') bits.push((n.formats || []).join(', '));
      else if (n.type !== 'file' && n.type !== 'fileset') bits.push(plural(n.files, 'file'));
      if (n.size) bits.push(size(n.size));
      if ((n.type === 'file' || n.type === 'fileset') && n.file !== undefined) bits.unshift(data.files[n.file][KIND]);
      return bits.join(' · ');
    }
    function treeNode(n) {
      var li = el('li', 'node node-' + n.type + (n.look ? ' node-look' : ''));
      var d = el('details');
      var s = el('summary');
      s.appendChild(el('span', 'node-icon', ICON[n.type] || ''));
      s.appendChild(el('span', 'node-name', n.name));
      s.appendChild(el('span', 'node-meta', nodeMeta(n)));
      var badge = n.type === 'file' || n.type === 'fileset' ? lookBadge(n.look > 0) : lookBadge(n.look);
      if (badge) s.appendChild(badge);
      d.appendChild(s);
      onFirstOpen(d, function () {
        if (n.file !== undefined) { var box = el('div'); d.appendChild(box); fillDetails(box, n.file); }
        if (n.children && n.children.length) {
          var ul = el('ul', 'tree');
          chunked(ul, n.children, treeNode, CHUNK);
          d.appendChild(ul);
        }
      });
      li.appendChild(d);
      return li;
    }
    function fileCard(i) {
      var row = data.files[i];
      var li = el('li', 'item' + (row[LOOK] ? ' item-look' : ''));
      var d = el('details');
      var s = el('summary');
      var head = el('div', 'item-head');
      var badge = lookBadge(!!row[LOOK]); if (badge) head.appendChild(badge);
      head.appendChild(pathLabel(row[PATH]));
      s.appendChild(head);
      s.appendChild(el('span', 'item-meta', [row[KIND], row[SIZE]].filter(Boolean).join(' · ')));
      d.appendChild(s);
      onFirstOpen(d, function () { var box = el('div'); d.appendChild(box); fillDetails(box, i); });
      li.appendChild(d);
      return li;
    }
    function render() {
      c.view.textContent = '';
      c.pager.hidden = true;
      c.lookLabel.textContent = 'Only files that need a look (' + data.counts.files_look.toLocaleString() + ')';
      var q = state.q.trim().toLowerCase();
      if (q || state.lookOnly) {
        var matches = [];
        data.files.forEach(function (row, i) {
          if ((!state.lookOnly || row[LOOK]) && (!q || (row[PATH] + ' ' + row[KIND]).toLowerCase().indexOf(q) !== -1)) matches.push(i);
        });
        matches.sort(function (a, b) { return data.files[a][RANK] - data.files[b][RANK]; });
        pagedList(c, state, matches, fileCard, 'file');
        return;
      }
      var tree = data.tree;
      var top = el('div', 'tree-root');
      top.appendChild(el('span', 'node-icon', ICON.folder));
      top.appendChild(el('strong', null, tree.name || opts.kbName));
      top.appendChild(el('span', 'node-meta', plural(tree.files, 'file') + (tree.size ? ' · ' + size(tree.size) : '')));
      c.view.appendChild(top);
      var ul = el('ul', 'tree tree-top');
      chunked(ul, tree.children, treeNode, CHUNK);
      c.view.appendChild(ul);
      c.status.textContent = 'Open a folder to see inside it, and a file to see what the croissant says about it.';
    }
    c.q.addEventListener('input', function () { state.q = c.q.value; state.page = 0; render(); });
    c.look.addEventListener('change', function () { state.lookOnly = c.look.checked; state.page = 0; render(); });
    c.prev.addEventListener('click', function () { state.page--; render(); root.scrollIntoView(); });
    c.next.addEventListener('click', function () { state.page++; render(); root.scrollIntoView(); });
    getJSON(opts.treeUrl).then(function (d) { data = d; render(); })
      .catch(function (e) { c.status.textContent = 'Sorry, the files didn’t load (' + e.message + ').'; });
  }

  // ── Tables: kinds of tables (one set of columns each) ────────────────────
  function tables(root, opts) {
    var c = controls(root, 'Search table kinds, columns and files\u2026');
    var state = { q: new URLSearchParams(location.search).get('q') || '', lookOnly: false, page: 0 };
    c.q.value = state.q;
    var data = null;

    function kindIssues(kind) {
      var seen = {};
      kind.tables.forEach(function (i) {
        data.tables[i].issues.forEach(function (x) {
          var key = x.severity + x.title;
          if (!seen[key]) seen[key] = { severity: x.severity, title: x.title, count: 0 };
          seen[key].count++;
        });
      });
      return Object.keys(seen).map(function (k) { return seen[k]; });
    }
    function kindUnsure(kind) {
      var out = [];
      kind.tables.slice(0, 5).forEach(function (i) { out = out.concat(data.tables[i].unsure); });
      return out.slice(0, 5);
    }
    // Files with the same columns that disagree on a column's kind of value (guessed from each file's first rows).
    function typeNotes(kind) {
      if (!kind.types_vary || !kind.types_vary.length) return null;
      var box = el('div', 'type-notes');
      box.appendChild(el('p', 'small', 'These files have the same columns, but the kind of value we recorded for some ' +
        'columns differs from file to file. Usually that means a column is mostly numbers with some text, or some files ' +
        'are mostly empty. Please tell us the right kind if you know it.'));
      var ul = el('ul', 'small');
      kind.types_vary.forEach(function (v) {
        var li = el('li');
        li.appendChild(el('code', null, v.column));
        li.appendChild(document.createTextNode(': ' + v.types.map(function (t) {
          return (t[0] || 'unknown') + ' in ' + t[1].toLocaleString();
        }).join(', ')));
        ul.appendChild(li);
      });
      box.appendChild(ul);
      return box;
    }
    function fileList(paths) {
      var box = el('div', 'family-files');
      box.appendChild(el('p', 'small muted', paths.length === 1 ? 'In this file:' : 'In these ' + paths.length.toLocaleString() + ' files:'));
      var ul = el('ul', 'file-list');
      chunked(ul, paths, function (p) { var li = el('li'); li.appendChild(pathLabel(p)); return li; }, FILE_LIST);
      box.appendChild(ul);
      return box;
    }
    function kindCard(kind, inGroup) {
      var li = el('li', 'item' + (kind.look ? ' item-look' : '') + (inGroup ? ' item-in-group' : ''));
      var d = el('details');
      var s = el('summary');
      var head = el('div', 'item-head');
      var badge = lookBadge(kind.look); if (badge) head.appendChild(badge);
      // Inside a group every kind has the group's name, so lead with what tells them apart.
      head.appendChild(el('strong', 'kind-title', inGroup ? plural(kind.columns.length, 'column') : kind.title));
      s.appendChild(head);
      var meta = el('span', 'item-meta', (inGroup ? '' : plural(kind.columns.length, 'column') + ' \u00b7 ') +
                    plural(kind.files.length, 'file') + (kind.example ? ' \u00b7 e.g. ' : ''));
      if (kind.example) meta.appendChild(el('span', 'example', kind.example));
      s.appendChild(meta);
      d.appendChild(s);
      onFirstOpen(d, function () {
        var body = el('div', 'item-body');
        var description = data.tables[kind.tables[0]].description;
        if (description && kind.tables.length === 1) body.appendChild(el('p', null, description));
        body.appendChild(chips(kind.columns));
        var notes = typeNotes(kind); if (notes) body.appendChild(notes);
        var issues = kindIssues(kind);
        if (kind.look && !issues.length) body.appendChild(el('p', 'why', WHY_FLAGGED));
        issuesAndDoubts(body, issues, kindUnsure(kind));
        body.appendChild(fileList(kind.files));
        if (kind.files.length > 1) {
          var named = el('p', 'small muted', 'Files named like ');
          named.appendChild(el('code', null, kind.name));
          body.appendChild(named);
        }
        body.appendChild(reportLink(opts.tableForm, kind.title + ' (' + kind.example + ')', 'Something wrong with these tables? Tell us \u2192'));
        d.appendChild(body);
      });
      li.appendChild(d);
      return li;
    }
    // Kinds that share a plain name, under one heading. Each kind inside is still its own set of columns.
    function groupCard(group) {
      if (group.kinds.length === 1) return kindCard(group.kinds[0], false);
      var look = group.kinds.reduce(function (n, k) { return n + (k.look ? 1 : 0); }, 0);
      var files = group.kinds.reduce(function (n, k) { return n + k.files.length; }, 0);
      var li = el('li', 'item item-group' + (look ? ' item-look' : ''));
      var d = el('details');
      var s = el('summary');
      var head = el('div', 'item-head');
      var badge = lookBadge(look); if (badge) head.appendChild(badge);
      head.appendChild(el('strong', 'kind-title', group.title));
      s.appendChild(head);
      s.appendChild(el('span', 'item-meta', group.kinds.length.toLocaleString() + ' kinds with different columns \u00b7 ' +
                                            plural(files, 'file')));
      d.appendChild(s);
      onFirstOpen(d, function () {
        var body = el('div', 'item-body');
        body.appendChild(el('p', 'small muted', 'These files have similar names but different columns, so each set of ' +
          'columns is listed as its own kind of table.'));
        var ul = el('ul', 'items');
        chunked(ul, group.kinds, function (k) { return kindCard(k, true); }, PAGE);
        body.appendChild(ul);
        d.appendChild(body);
      });
      li.appendChild(d);
      return li;
    }
    function groupByTitle(kinds) {
      var groups = [], byTitle = {};
      kinds.forEach(function (k) {
        if (!byTitle[k.title]) { byTitle[k.title] = { title: k.title, kinds: [] }; groups.push(byTitle[k.title]); }
        byTitle[k.title].kinds.push(k);
      });
      return groups;
    }
    function render() {
      c.view.textContent = '';
      c.pager.hidden = true;
      c.lookLabel.textContent = 'Only tables that need a look (' + data.counts.kinds_look.toLocaleString() + ' kinds)';
      var q = state.q.trim().toLowerCase();
      var has = function (t) { return t.toLowerCase().indexOf(q) !== -1; };
      var kinds = data.kinds.filter(function (kind) {
        if (state.lookOnly && !kind.look) return false;
        if (!q || has(kind.title) || has(kind.name)) return true;
        return kind.columns.some(function (col) { return has(col[0]); }) || kind.files.some(has);
      });
      var groups = groupByTitle(kinds);
      pagedList(c, state, groups, groupCard, 'name');
      if (kinds.length) {
        var start = state.page * PAGE, end = Math.min(groups.length, start + PAGE);
        c.status.textContent = plural(kinds.length, 'kind of table') + ' under ' + plural(groups.length, 'name') +
          (groups.length > PAGE ? ' (showing names ' + (start + 1).toLocaleString() + '\u2013' + end.toLocaleString() + ')' : '') + '.';
      }
    }
    c.q.addEventListener('input', function () { state.q = c.q.value; state.page = 0; render(); });
    c.look.addEventListener('change', function () { state.lookOnly = c.look.checked; state.page = 0; render(); });
    c.prev.addEventListener('click', function () { state.page--; render(); root.scrollIntoView(); });
    c.next.addEventListener('click', function () { state.page++; render(); root.scrollIntoView(); });
    getJSON(opts.url).then(function (d) { data = d; render(); })
      .catch(function (e) { c.status.textContent = 'Sorry, the tables didn\u2019t load (' + e.message + ').'; });
  }

  window.Boulangerie = { files: files, tables: tables };
})();
