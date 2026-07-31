from __future__ import annotations

import dataclasses
import json
import random
import re
import time
from pathlib import Path
from typing import Any

from .adapters import (
    RELATION_LABELS,
    SPECS,
    canonical_from_network,
    count_href_fragments,
    facebook_friend_filter_route,
    network_keywords,
    normalize_profile_link,
    relation_url,
    source_identity,
    threads_display_name,
    tiktok_canonical_url,
)
from .browser import CDPBrowser, CDPTab
from .util import parse_count, utc_now, write_json


@dataclasses.dataclass
class CollectionOutcome:
    platform: str
    source_profile_url: str
    relation: str
    reported_count: int | None
    reported_count_raw: str | None
    collected_this_run: int
    status: str
    reason: str
    started_at: str
    completed_at: str
    records: list[dict[str, Any]]
    diagnostics: dict[str, Any]


COUNT_SCAN_JS = r"""
(() => {
  const fragments = __FRAGMENTS__;
  const labels = __LABELS__;
  const selectors = __CONTROL_SELECTORS__;
  const visible = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const norm = value => (value || '').replace(/\s+/g, ' ').trim();
  const exactHref = href => {
    if (!fragments.length) return false;
    try {
      const url = new URL(href, location.href);
      const path = decodeURIComponent(url.pathname).replace(/\/+$/, '') || '/';
      const pathSearch = path + url.search;
      return fragments.some(value => {
        const fragment = decodeURIComponent(value).replace(/\/+$/, '');
        return path.toLowerCase() === fragment.toLowerCase() || pathSearch.toLowerCase() === fragment.toLowerCase();
      });
    } catch (_) {
      return false;
    }
  };
  const candidates = [];
  const elements = new Set(selectors.flatMap(selector => [...document.querySelectorAll(selector)]));
  for (const el of elements) {
    if (!visible(el)) continue;
    const href = el.href || el.getAttribute('href') || '';
    const text = norm([el.innerText, el.textContent, el.getAttribute('aria-label'), el.getAttribute('title')].filter(Boolean).join(' '));
    const lower = text.toLowerCase();
    let score = 0;
    const exact = exactHref(href);
    if (fragments.length && !exact) continue;
    if (exact) score += 200;
    if (labels.some(label => lower === label)) score += 100;
    if (labels.some(label => lower.includes(label))) score += 40;
    if (!score || text.length > 220) continue;
    const parentText = norm(el.parentElement?.innerText || '');
    candidates.push({href, text, parentText: parentText.slice(0, 260), score});
  }
  candidates.sort((a,b) => b.score - a.score);
  return candidates.slice(0, 30);
})()
"""

PINTEREST_ZERO_COUNT_JS = r"""
(() => {
  const labels = __LABELS__;
  const visible = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const norm = value => (value || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const matches = [];
  for (const el of document.querySelectorAll('div, span')) {
    if (!visible(el)) continue;
    const text = norm(el.innerText || el.textContent || '');
    if (!labels.some(label => text === `0 ${label}`)) continue;
    matches.push({text, tag: el.tagName, source: 'pinterest_visible_zero_count'});
  }
  return matches.length ? {ok: true, count: 0, raw: '0', ...matches[0]} : {ok: false};
})()
"""

CLICK_RELATION_JS = r"""
(() => {
  const fragments = __FRAGMENTS__;
  const labels = __LABELS__;
  const selectors = __CONTROL_SELECTORS__;
  const visible = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const norm = value => (value || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const exactHref = href => {
    if (!fragments.length) return false;
    try {
      const url = new URL(href, location.href);
      const path = decodeURIComponent(url.pathname).replace(/\/+$/, '') || '/';
      const pathSearch = path + url.search;
      return fragments.some(value => {
        const fragment = decodeURIComponent(value).replace(/\/+$/, '');
        return path.toLowerCase() === fragment.toLowerCase() || pathSearch.toLowerCase() === fragment.toLowerCase();
      });
    } catch (_) {
      return false;
    }
  };
  const choices = [];
  const elements = new Set(selectors.flatMap(selector => [...document.querySelectorAll(selector)]));
  for (const el of elements) {
    if (!visible(el)) continue;
    const href = el.href || el.getAttribute('href') || '';
    const text = norm([el.innerText, el.textContent, el.getAttribute('aria-label'), el.getAttribute('title')].filter(Boolean).join(' '));
    let score = 0;
    const exact = exactHref(href);
    if (fragments.length && !exact) continue;
    if (exact) score += 200;
    if (labels.some(label => text === label)) score += 120;
    if (labels.some(label => text.startsWith(label + ' ') || text.endsWith(' ' + label))) score += 80;
    if (labels.some(label => text.includes(label))) score += 30;
    if (text.length > 180) score -= 80;
    if (score > 0) choices.push({el, href, text, score});
  }
  choices.sort((a,b) => b.score - a.score);
  if (!choices.length) return {clicked:false, candidates:[]};
  const choice = choices[0];
  choice.el.scrollIntoView({block:'center', inline:'center'});
  choice.el.click();
  return {clicked:true, href:choice.href, text:choice.text, candidates:choices.slice(0,10).map(x => ({href:x.href,text:x.text,score:x.score}))};
})()
"""

TRUSTED_RELATION_TARGET_JS = r"""
(() => {
  const labels = __LABELS__;
  const selectors = __CONTROL_SELECTORS__;
  const visible = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const norm = value => (value || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const choices = [];
  const elements = new Set(selectors.flatMap(selector => [...document.querySelectorAll(selector)]));
  for (const el of elements) {
    if (!visible(el)) continue;
    const text = norm(el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title'));
    if (!text || text.length > 100) continue;
    let score = 0;
    for (const label of labels) {
      const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      if (new RegExp(`^(?:[0-9][0-9,.]*\\s+)?${escaped}$`, 'i').test(text)
          || new RegExp(`^${escaped}\\s+[0-9][0-9,.]*$`, 'i').test(text)) score = Math.max(score, 220);
      else if (text === label) score = Math.max(score, 180);
    }
    if (!score) continue;
    const target = el.closest('a[href],button,[role="button"],[role="link"]') || el;
    const cursor = getComputedStyle(target).cursor;
    if (!['A', 'BUTTON'].includes(target.tagName) && !['button', 'link'].includes(target.getAttribute('role')) && cursor !== 'pointer') continue;
    const r = target.getBoundingClientRect();
    choices.push({target, text, score, cursor, rect:{x:r.x,y:r.y,width:r.width,height:r.height}});
  }
  choices.sort((a,b) => b.score-a.score || (a.rect.width*a.rect.height)-(b.rect.width*b.rect.height));
  if (!choices.length) return {found:false,candidates:[]};
  const choice = choices[0];
  choice.target.scrollIntoView({block:'center',inline:'center'});
  const r = choice.target.getBoundingClientRect();
  return {
    found:true,
    text:choice.text,
    x:r.left+r.width/2,
    y:r.top+r.height/2,
    rect:{x:r.x,y:r.y,width:r.width,height:r.height},
    candidates:choices.slice(0,10).map(item => ({text:item.text,score:item.score,cursor:item.cursor,rect:item.rect}))
  };
})()
"""

FACEBOOK_FRIEND_COUNT_JS = r"""
(() => {
  const source = new URL(__SOURCE__);
  const expected = source.pathname.replace(/\/+$/, '') + '/friends';
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const matches = [];
  for (const anchor of document.querySelectorAll('a[href]')) {
    if (!visible(anchor)) continue;
    let url;
    try { url = new URL(anchor.href, location.href); } catch (_) { continue; }
    if (url.pathname.replace(/\/+$/, '') !== expected) continue;
    for (let node = anchor; node && node !== document.body; node = node.parentElement) {
      const text = clean(node.innerText || '');
      if (text.length > 1200) break;
      const found = text.match(/(?:^|\s)([0-9][0-9,]*)\s+friends?(?:\s|$)/i);
      if (found) matches.push({raw:found[1], text, length:text.length});
    }
  }
  matches.sort((a,b) => a.length - b.length);
  return matches[0] || null;
})()
"""

LIST_STATE_JS = r"""
(() => {
  const selectors = __SELECTORS__;
  const platform = __PLATFORM__;
  const relation = __RELATION__;
  const visible = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const anchors = [];
  const seen = new Set();
  let relationshipFloor = null;
  let relationshipRight = null;
  if (platform === 'facebook') {
    const activeTab = [...document.querySelectorAll('a[role="tab"][aria-selected="true"]')]
      .find(el => {
        const text = (el.innerText || '').trim().toLowerCase();
        if (text === relation || (relation === 'friends' && text === 'all friends')) return true;
        if (relation !== 'friends') return false;
        try {
          const url = new URL(el.href, location.href);
          const section = (url.searchParams.get('sk') || url.pathname.split('/').filter(Boolean).pop() || '').toLowerCase();
          return section.startsWith('friends_') && section !== 'friends_all';
        } catch (_) {
          return false;
        }
      });
    if (activeTab) relationshipFloor = activeTab.getBoundingClientRect().bottom;
  }
  if (platform === 'quora' && relation === 'followers' && /\/profile\/[^/]+\/followers\/?$/i.test(location.pathname)) {
    const activeTab = [...document.querySelectorAll('[role="tab"]')]
      .filter(visible)
      .find(el => /^\d[\d,]*\s+followers?$/i.test((el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim()) && el.getBoundingClientRect().y > 100);
    if (activeTab) {
      relationshipFloor = activeTab.getBoundingClientRect().bottom;
      relationshipRight = activeTab.parentElement?.getBoundingClientRect().right || null;
    }
  }
  for (const selector of selectors) {
    for (const a of document.querySelectorAll(selector)) {
      if (!a.href || !visible(a)) continue;
      if (platform === 'facebook') {
        const rect = a.getBoundingClientRect();
        if (relationshipFloor === null || rect.bottom <= relationshipFloor + 2 || a.getAttribute('role') === 'tab') continue;
      }
      if (platform === 'quora') {
        const rect = a.getBoundingClientRect();
        if (relationshipFloor === null || rect.bottom <= relationshipFloor + 2) continue;
        if (relationshipRight !== null && rect.left >= relationshipRight + 2) continue;
      }
      const key = a.href + '|' + (a.innerText || '');
      if (seen.has(key)) continue;
      seen.add(key);
      anchors.push(a);
    }
  }

  let root = window.__contactAnalyzerRoot;
  if (!root || !document.contains(root)) {
    const first = anchors[0];
    let current = first;
    let chosen = null;
    while (current && current !== document.body) {
      const style = getComputedStyle(current);
      if (current.scrollHeight > current.clientHeight + 40 && ['auto','scroll'].includes(style.overflowY)) {
        chosen = current;
        break;
      }
      current = current.parentElement;
    }
    if (!chosen) {
      const dialog = document.querySelector('[role="dialog"]');
      if (dialog) {
        const scrollables = [...dialog.querySelectorAll('*')].filter(el => {
          const style = getComputedStyle(el);
          return el.scrollHeight > el.clientHeight + 40 && ['auto','scroll'].includes(style.overflowY);
        }).sort((a,b) => (b.clientWidth*b.clientHeight) - (a.clientWidth*a.clientHeight));
        chosen = scrollables[0] || null;
      }
    }
    root = chosen || document.scrollingElement || document.documentElement;
    window.__contactAnalyzerRoot = root;
  }

  const records = anchors.map(a => {
    const row = a.closest('[data-testid="UserCell"],li,[role="listitem"],article,.d-table,.account,.userBadgeListItem') || a.parentElement || a;
    const img = row.querySelector('img') || a.querySelector('img');
    return {
      href: a.href,
      anchorText: (a.innerText || '').replace(/\s+/g,' ').trim().slice(0,180),
      itemText: (row.innerText || a.innerText || '').replace(/\s+/g,' ').trim().slice(0,600),
      imageSrc: img?.currentSrc || img?.src || '',
      imageAlt: img?.alt || ''
    };
  });

  const documentRoot = root === document.scrollingElement || root === document.documentElement || root === document.body;
  const scrollTop = documentRoot ? window.scrollY : root.scrollTop;
  const scrollHeight = documentRoot ? Math.max(document.body.scrollHeight, document.documentElement.scrollHeight) : root.scrollHeight;
  const clientHeight = documentRoot ? window.innerHeight : root.clientHeight;
  const bodyText = (document.body?.innerText || '').toLowerCase();
  const spinner = [...document.querySelectorAll('[role="progressbar"],[aria-busy="true"],svg[aria-label*="Loading"],div[data-testid="cellInnerDiv"] [role="progressbar"],[data-visualcompletion="loading-state"],[role="status"][aria-label*="Loading"]')]
    .some(visible);
  const blockedTerms = ['verify you are human','unusual activity','temporarily limited','rate limit exceeded','try again later','checkpoint required'];
  return {
    records,
    scrollTop,
    scrollHeight,
    clientHeight,
    atEnd: scrollTop + clientHeight >= scrollHeight - 3,
    documentRoot,
    spinner,
    blockedText: blockedTerms.find(term => bodyText.includes(term)) || null,
    url: location.href,
    title: document.title
  };
})()
"""

QUORA_FOLLOWER_COUNT_JS = r"""
(() => {
  const source = new URL(__SOURCE__);
  const current = new URL(location.href);
  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const sourcePath = source.pathname.replace(/\/+$/, '');
  if (current.pathname.replace(/\/+$/, '') !== sourcePath || !/^\/profile\/[^/]+$/i.test(sourcePath)) {
    return {ok:false, reason:'not_source_quora_profile', url:location.href};
  }
  const tab = [...document.querySelectorAll('[role="tab"]')]
    .filter(visible)
    .find(el => /^\d[\d,]*\s+followers?$/i.test(clean(el.innerText || el.textContent)) && el.getBoundingClientRect().y > 100);
  if (!tab) return {ok:false, reason:'exact_source_follower_tab_not_found', url:location.href};
  const text = clean(tab.innerText || tab.textContent);
  const match = text.match(/^(\d[\d,]*)\s+followers?$/i);
  const raw = match ? match[1] : null;
  const count = raw === null ? null : Number(raw.replace(/,/g, ''));
  return {ok:Number.isFinite(count), count:Number.isFinite(count) ? count : null, raw, text, url:location.href};
})()
"""

FACEBOOK_FRIEND_FILTER_TABS_JS = r"""
(() => {
  const visible = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  return [...document.querySelectorAll('a[role="tab"][href]')]
    .filter(visible)
    .map(el => ({
      text: (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim(),
      href: el.href || '',
      selected: el.getAttribute('aria-selected') === 'true'
    }));
})()
"""

SCROLL_JS = r"""
(() => {
  const selectors = __SELECTORS__;
  let root = window.__contactAnalyzerRoot || document.scrollingElement || document.documentElement;
  const anchors = [];
  for (const selector of selectors) anchors.push(...document.querySelectorAll(selector));
  const visible = anchors.filter(a => {
    const r = a.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  });
  const last = visible[visible.length - 1];
  if (last) last.scrollIntoView({block:'end'});
  const isDoc = root === document.scrollingElement || root === document.documentElement || root === document.body;
  const before = isDoc ? window.scrollY : root.scrollTop;
  const step = Math.max(650, Math.floor((isDoc ? window.innerHeight : root.clientHeight) * 0.88));
  if (isDoc) {
    window.scrollBy(0, step);
    if (window.scrollY === before) window.scrollTo(0, Math.max(document.body.scrollHeight, document.documentElement.scrollHeight));
  } else {
    root.scrollTop = Math.min(root.scrollHeight, before + step);
    root.dispatchEvent(new Event('scroll', {bubbles:true}));
  }
  return {before, after:isDoc ? window.scrollY : root.scrollTop};
})()
"""

X_SCROLL_TARGET_JS = r"""
(() => {
  const relation = __RELATION__;
  const wanted = relation === 'followers' ? 'timeline: followers' : 'timeline: following';
  const visible = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const timelines = [...document.querySelectorAll('main [aria-label]')]
    .filter(el => visible(el) && (el.getAttribute('aria-label') || '').trim().toLowerCase() === wanted);
  const target = timelines[0] || document.querySelector('main') || document.documentElement;
  const r = target.getBoundingClientRect();
  const width = Math.max(2, window.innerWidth || document.documentElement.clientWidth || 2);
  const height = Math.max(2, window.innerHeight || document.documentElement.clientHeight || 2);
  return {
    x: Math.max(1, Math.min(width - 1, r.left + (r.width / 2))),
    y: Math.max(1, Math.min(height - 1, Math.max(r.top + 1, Math.min(r.bottom - 1, height * 0.78)))),
    timelineFound: Boolean(timelines.length),
    ariaLabel: timelines[0]?.getAttribute('aria-label') || null
  };
})()
"""

NEXT_PAGE_JS = r"""
(() => {
  const selectors = __SELECTORS__;
  const visible = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  for (const selector of selectors) {
    const items = [...document.querySelectorAll(selector)].filter(visible);
    for (const el of items) {
      if (el.matches('[disabled],.disabled,[aria-disabled="true"]')) continue;
      const href = el.href || '';
      const text = (el.innerText || el.getAttribute('aria-label') || '').replace(/\s+/g,' ').trim();
      if (href) return {clicked:false, navigate:true, href, text, selector};
      el.scrollIntoView({block:'center'});
      el.click();
      return {clicked:true, href, text, selector};
    }
  }
  return {clicked:false};
})()
"""

TIKTOK_MODAL_JS = r"""
(() => {
  const relation = __RELATION__;
  const source = __SOURCE__;
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  const subject = (new URL(source)).pathname.split('/').filter(Boolean).pop().replace(/^@/, '').toLowerCase();
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const modal = [...document.querySelectorAll('[role="dialog"], [aria-modal="true"]')]
    .filter(visible)
    .find(el => {
      const text = clean(el.innerText || '').toLowerCase();
      return text.includes(subject) && text.includes('following') && text.includes('followers') && text.includes('suggested');
    });
  const exact = new RegExp('^(?:' + relation + '(?:\\s+([0-9][0-9,.]*[kmb]?))?|([0-9][0-9,.]*[kmb]?)\\s+' + relation + ')$', 'i');
  const controls = modal ? [...modal.querySelectorAll('*')]
    .filter(el => visible(el) && (['A','BUTTON'].includes(el.tagName) || ['tab','button','link'].includes(el.getAttribute('role')) || getComputedStyle(el).cursor === 'pointer'))
    .map(el => {
      const own = clean([el.innerText, el.getAttribute('aria-label')].filter(Boolean).join(' '));
      const parent = clean(el.parentElement?.innerText || '');
      return {el, text: exact.test(own) ? own : exact.test(parent) ? parent : own,
        selected: el.getAttribute('aria-selected') === 'true' || el.getAttribute('data-active') === 'true' || /active|selected/i.test(el.className || '')};
    }) : [];
  let control = controls.find(item => exact.test(item.text));
  let reported = null, reportedRaw = null;
  if (control) {
    const match = control.text.match(exact);
    reportedRaw = match && (match[1] || match[2]) ? (match[1] || match[2]) : null;
    if (reportedRaw) {
      const n = reportedRaw.replace(/,/g, '').toLowerCase();
      reported = n.endsWith('k') ? Math.round(parseFloat(n) * 1e3) : n.endsWith('m') ? Math.round(parseFloat(n) * 1e6) : n.endsWith('b') ? Math.round(parseFloat(n) * 1e9) : parseInt(n, 10);
    }
  }
  if (!modal) {
    const counted = new RegExp('^(?:' + relation + '\\s+[0-9][0-9,.]*[kmb]?|[0-9][0-9,.]*[kmb]?\\s+' + relation + ')$', 'i');
    const subjectNode = [...document.querySelectorAll('[data-e2e="user-subtitle"],h1,h2')]
      .filter(visible).find(el => clean(el.innerText || '').replace(/^@/, '').toLowerCase() === subject);
    let header = null;
    for (let node = subjectNode; node && node !== document.body && !header; node = node.parentElement) {
      const text = clean(node.innerText || '').toLowerCase();
      const stat = [...node.querySelectorAll(`[data-e2e="${relation}"]`)].filter(visible)
        .find(el => visible(el.parentElement) && counted.test(clean(el.parentElement.innerText || '')));
      if (text.includes(subject) && text.includes('following') && text.includes('followers') && stat) header = node;
    }
    const stat = header && [...header.querySelectorAll(`[data-e2e="${relation}"]`)].filter(visible)
      .find(el => visible(el.parentElement) && counted.test(clean(el.parentElement.innerText || '')));
    let target = stat?.parentElement || stat;
    for (let node = stat?.parentElement; node && node !== header; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (['A','BUTTON'].includes(node.tagName) || ['button','link'].includes(node.getAttribute('role')) || style.cursor === 'pointer') { target = node; break; }
    }
    const profileControl = target ? {el:target, text:clean(stat.parentElement.innerText)} : null;
    const candidates = header ? [...header.querySelectorAll(`[data-e2e="${relation}"],button,a,[role="button"],[role="link"]`)]
      .filter(visible).map(el => ({tag:el.tagName, role:el.getAttribute('role') || '', text:clean(el.innerText || ''), href:el.href || '', rect:el.getBoundingClientRect().toJSON(), cursor:getComputedStyle(el).cursor})) : [];
    if (profileControl) { profileControl.el.scrollIntoView({block:'center', inline:'center'}); const r=profileControl.el.getBoundingClientRect(); return {ok:false, opening:true, x:r.left+r.width/2, y:r.top+r.height/2, ix:r.left+r.width/2, iy:r.top+r.height*.3, reason:'modal_opening', url:location.href, clickedText:profileControl.text, candidates}; }
    return {ok:false, reason:'exact_profile_header_control_not_found', url:location.href, candidates};
  }
  if (!modal || !control) return {ok:false, reason: modal ? 'exact_relation_control_not_found' : 'relationship_modal_not_found', url:location.href};
  control.el.focus?.();
  control.el.scrollIntoView({block:'center', inline:'center'});
  const r = control.el.getBoundingClientRect();
  const modalText = clean(modal.innerText);
  const privateList = /this account(?:\u2019|')s (?:following|followers) list is private|(?:following|followers) list is currently hidden/i.test(modalText);
  return {ok:true, relation, reported, reportedRaw, active:control.selected ? relation : null,
    privateList, x:r.left+r.width/2, y:r.top+r.height/2,
    modalText: modalText.slice(0, 1200), url:location.href};
})()
"""

TIKTOK_LIST_STATE_JS = r"""
(() => {
  const relation = __RELATION__;
  const source = __SOURCE__;
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  const subject = (new URL(source)).pathname.split('/').filter(Boolean).pop().replace(/^@/, '').toLowerCase();
  const visible = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; };
  const modal = [...document.querySelectorAll('[role="dialog"], [aria-modal="true"]')].filter(visible).find(el => {
    const text = clean(el.innerText || '').toLowerCase(); return text.includes(subject) && text.includes('following') && text.includes('followers') && text.includes('suggested');
  });
  if (!modal) return {ok:false, reason:'relationship_modal_not_found', url:location.href, records:[]};
  const scrollables = [...modal.querySelectorAll('*')].filter(el => {
    const s = getComputedStyle(el); return visible(el) && el.scrollHeight > el.clientHeight + 8 && ['auto','scroll'].includes(s.overflowY);
  }).sort((a,b) => (b.clientWidth*b.clientHeight) - (a.clientWidth*a.clientHeight));
  const root = scrollables[0] || modal;
  if (!root) return {ok:false, reason:'modal_scroll_container_not_found', url:location.href, records:[]};
  const nodes = [...root.querySelectorAll('a[href*="/@"], [data-e2e*="user" i], [data-e2e*="row" i]')].filter(visible);
  const rows = [], seen = new Set();
  for (const node of nodes) {
    const row = node.closest('[role="listitem"],li,[data-e2e*="row" i]') || node.parentElement || node;
    if (!visible(row)) continue;
    const href = node.href || node.getAttribute('href') || '';
    const text = clean(row.innerText || node.innerText || '');
    const match = href.match(new RegExp('(?:https?://[^/]+)?/@([A-Za-z0-9._~-]+)', 'i')) || text.match(new RegExp('(?:^|\\s)@([A-Za-z0-9._~-]+)', 'i'));
    const username = match ? match[1] : null;
    if (!username || seen.has(username.toLowerCase())) continue;
    seen.add(username.toLowerCase());
    rows.push({href, username, text:text.slice(0, 500)});
  }
  return {ok:true, relation, url:location.href, records:rows, scrollTop:root.scrollTop || 0, scrollHeight:root.scrollHeight || 0, clientHeight:root.clientHeight || 0, atEnd:(root.scrollTop || 0) + (root.clientHeight || 0) >= (root.scrollHeight || 0) - 3, rootFound:true, fallbackRoot:!scrollables.length};
})()
"""

TIKTOK_SCROLL_JS = r"""
(() => {
  const modal = [...document.querySelectorAll('[role="dialog"], [aria-modal="true"]')].find(el => { const r=el.getBoundingClientRect(); const t=(el.innerText||'').toLowerCase(); return r.width>0 && r.height>0 && t.includes('following') && t.includes('followers') && t.includes('suggested'); });
  if (!modal) return {ok:false};
  const root = [...modal.querySelectorAll('*')].filter(el => { const s=getComputedStyle(el); return el.scrollHeight > el.clientHeight + 8 && ['auto','scroll'].includes(s.overflowY); }).sort((a,b)=>(b.clientWidth*b.clientHeight)-(a.clientWidth*a.clientHeight))[0];
  if (!root) return {ok:false};
  const before = root.scrollTop;
  root.scrollTop = Math.min(root.scrollHeight, before + Math.max(1, Math.floor(root.clientHeight * 0.8)));
  root.dispatchEvent(new Event('scroll', {bubbles:true}));
  return {ok:true, before, after:root.scrollTop};
})()
"""

TIKTOK_PROFILE_PRIVATE_STATE_JS = r"""
(() => {
  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
  const body = clean(document.body?.innerText || '');
  const privateProfile = /\bthis account is private\b/i.test(body);
  return {
    private: privateProfile,
    evidence: privateProfile ? 'This account is private' : null,
    url: location.href,
  };
})()
"""

TIKTOK_PROFILE_UNAVAILABLE_STATE_JS = r"""
(() => {
  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
  const body = clean(document.body?.innerText || '');
  const unavailable = /\bcouldn(?:\u2019|')t find this account\b|\baccount not found\b/i.test(body);
  return {
    unavailable,
    evidence: unavailable ? body.slice(0, 1200) : null,
    url: location.href,
    title: document.title,
  };
})()
"""

INSTAGRAM_PRIVATE_STATE_JS = r"""
(() => {
  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
  const body = clean(document.body?.innerText || '');
  const isPrivate = /\bthis profile is private\b/i.test(body);
  if (!isPrivate) return {private:false, url:location.href};
  const counts = {};
  for (const relation of ['followers', 'following']) {
    const exact = new RegExp('^(?:([0-9][0-9,.]*)\\s+' + relation + '|' + relation + '\\s+([0-9][0-9,.]*))$', 'i');
    const matches = [...document.querySelectorAll('main a,main [role="link"],main span,main div')]
      .map(el => clean(el.innerText || el.textContent || ''))
      .filter(text => text.length <= 80 && exact.test(text));
    const match = matches[0]?.match(exact);
    if (match) counts[relation] = match[1] || match[2];
  }
  return {private:true, counts, url:location.href, evidence:'This profile is private'};
})()
"""

FACEBOOK_RELATION_UNAVAILABLE_STATE_JS = r"""
(() => {
  const relation = __RELATION__;
  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
  const expected = relation === 'friends'
    ? /^no friends to show$/i
    : relation === 'followers'
      ? /^no followers to show$/i
      : /^no following to show$/i;
  const evidence = [...document.querySelectorAll('main *, [role="main"] *')]
    .filter(el => {
      const rect = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
    })
    .map(el => clean(el.innerText || el.textContent || ''))
    .find(text => expected.test(text));
  return {unavailable:Boolean(evidence), evidence:evidence || null, url:location.href};
})()
"""

GITHUB_EMPTY_STATE_JS = r"""
(() => {
  const relation = __RELATION__;
  const text = (document.body?.innerText || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const empty = relation === 'followers'
    ? text.includes("doesn’t have any followers yet") || text.includes("doesn't have any followers yet")
    : text.includes("isn’t following anybody") || text.includes("isn't following anybody");
  return {empty, text: empty ? text.slice(0, 1000) : ''};
})()
"""

X_PROFILE_UNAVAILABLE_STATE_JS = r"""
(() => {
  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
  const body = clean(document.body?.innerText || '');
  const unavailable = /\bthis account (?:doesn\u2019t|doesn't) exist\b|\baccount suspended\b/i.test(body);
  return {
    unavailable,
    evidence: unavailable ? body.slice(0, 1200) : null,
    url: location.href,
    title: document.title,
  };
})()
"""

THREADS_PROFILE_STATE_JS = r"""
(() => {
  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const body = clean(document.body?.innerText || '');
  const exact = /^(?:([0-9][0-9,.]*)\s+followers|followers\s+([0-9][0-9,.]*))$/i;
  const matches = [...document.querySelectorAll('main a,main [role="button"],main span,main div')]
    .filter(visible)
    .map(el => clean(el.innerText || el.textContent || ''))
    .filter(text => text.length <= 80 && exact.test(text));
  const match = matches[0]?.match(exact);
  const bodyMatch = body.match(/(?:^|\s)([0-9][0-9,.]*)\s+followers(?:\s|$)/i);
  const raw = match ? (match[1] || match[2]) : (bodyMatch?.[1] || null);
  return {
    private: /\bthis profile is private\b/i.test(body),
    followersRaw: raw,
    followers: raw === null ? null : parseInt(raw.replace(/,/g, ''), 10),
    evidence: body.slice(0, 1200),
    url: location.href,
  };
})()
"""

THREADS_PROFILE_CONTROL_JS = r"""
(() => {
  const source = new URL(__SOURCE__);
  const subject = decodeURIComponent(source.pathname).split('/').filter(Boolean).pop().replace(/^@/, '').toLowerCase();
  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
  const visible = el => { const r=el.getBoundingClientRect(),s=getComputedStyle(el); return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'; };
  if (decodeURIComponent(location.pathname).replace(/\/+$/, '').toLowerCase() !== decodeURIComponent(source.pathname).replace(/\/+$/, '').toLowerCase())
    return {ok:false, reason:'source_profile_navigation_failed', url:location.href};
  const body = clean(document.body?.innerText || '').toLowerCase();
  if (!body.includes(subject)) return {ok:false, reason:'source_username_not_visible', url:location.href};
  const exact = /^(?:([0-9][0-9,.]*)\s+followers|followers\s+([0-9][0-9,.]*))$/i;
  const choices = [...document.querySelectorAll('[role="button"],button,a')].filter(visible).map(el => {
    const text=clean([el.innerText,el.getAttribute('aria-label')].filter(Boolean).join(' '));
    const match=text.match(exact), r=el.getBoundingClientRect();
    return {el,text,match,r,score:(el.getAttribute('role')==='button'?100:0)+(getComputedStyle(el).cursor==='pointer'?50:0)-r.width*r.height/100000};
  }).filter(item => item.match).sort((a,b)=>b.score-a.score);
  if (!choices.length) return {ok:false, reason:'exact_threads_followers_control_not_found', url:location.href};
  const choice=choices[0], raw=choice.match[1]||choice.match[2];
  choice.el.focus?.(); choice.el.scrollIntoView({block:'center',inline:'center'});
  const r=choice.el.getBoundingClientRect();
  return {ok:true, reportedRaw:raw, reported:parseInt(raw.replace(/,/g,''),10), text:choice.text, x:r.left+r.width/2, y:r.top+r.height/2, url:location.href};
})()
"""

THREADS_MODAL_JS = r"""
(() => {
  const relation=__RELATION__, source=new URL(__SOURCE__);
  const clean=value=>String(value||'').replace(/\s+/g,' ').trim();
  const visible=el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
  if (decodeURIComponent(location.pathname).replace(/\/+$/,'').toLowerCase() !== decodeURIComponent(source.pathname).replace(/\/+$/,'').toLowerCase())
    return {ok:false,reason:'navigation_guard_triggered',url:location.href};
  const dialog=[...document.querySelectorAll('[role="dialog"],[aria-modal="true"]')].filter(visible).find(el=>{
    const text=clean(el.innerText||''); return /followers\s+[0-9]/i.test(text)&&/following\s+[0-9]/i.test(text);
  });
  if (!dialog) return {ok:false,reason:'threads_relationship_modal_not_found',url:location.href};
  const exact=name=>new RegExp('^'+name+'\\s+([0-9][0-9,.]*)$','i');
  const tabs=[...dialog.querySelectorAll('[role="tab"]')].filter(visible).map(el=>({el,text:clean(el.innerText||''),selected:el.getAttribute('aria-selected')==='true'}));
  const followers=tabs.find(item=>exact('followers').test(item.text));
  const following=tabs.find(item=>exact('following').test(item.text));
  if (!followers||!following) return {ok:false,reason:'threads_exact_tabs_not_found',url:location.href};
  const requested=relation==='followers'?followers:following;
  const match=requested.text.match(exact(relation)), raw=match?.[1]||null;
  const button=[...requested.el.querySelectorAll('[role="button"],button')].filter(visible)
    .find(el=>clean(el.getAttribute('aria-label')||'').toLowerCase()===relation)||requested.el;
  button.focus?.(); const r=button.getBoundingClientRect();
  const active=(followers.selected?'followers':following.selected?'following':null);
  return {ok:true,relation,active,reportedRaw:raw,reported:raw?parseInt(raw.replace(/,/g,''),10):null,
    followersCount:parseInt(followers.text.match(exact('followers'))[1].replace(/,/g,''),10),
    followingCount:parseInt(following.text.match(exact('following'))[1].replace(/,/g,''),10),
    x:r.left+r.width/2,y:r.top+r.height/2,url:location.href,modalText:clean(dialog.innerText||'').slice(0,1600)};
})()
"""

THREADS_LIST_STATE_JS = r"""
(() => {
  const relation=__RELATION__, source=new URL(__SOURCE__);
  const subject=decodeURIComponent(source.pathname).split('/').filter(Boolean).pop().replace(/^@/,'').toLowerCase();
  const clean=value=>String(value||'').replace(/\s+/g,' ').trim();
  const visible=el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
  const dialog=[...document.querySelectorAll('[role="dialog"],[aria-modal="true"]')].filter(visible).find(el=>/followers\s+[0-9]/i.test(clean(el.innerText||''))&&/following\s+[0-9]/i.test(clean(el.innerText||'')));
  if(!dialog)return {ok:false,reason:'threads_relationship_modal_not_found',url:location.href,records:[]};
  const active=[...dialog.querySelectorAll('[role="tab"][aria-selected="true"]')].find(visible);
  if(!active||!clean(active.innerText||'').toLowerCase().startsWith(relation))return {ok:false,reason:'threads_requested_tab_not_active',url:location.href,records:[]};
  const roots=[...dialog.querySelectorAll('*')].filter(el=>{const s=getComputedStyle(el);return visible(el)&&el.scrollHeight>el.clientHeight+8&&['auto','scroll'].includes(s.overflowY);}).sort((a,b)=>(b.clientWidth*b.clientHeight)-(a.clientWidth*a.clientHeight));
  // Small relationship lists can fit entirely in the modal, so Threads does
  // not create an overflowing child. The verified dialog is then the exact
  // relationship boundary and is safe to use as a non-scrolling root.
  const root=roots[0]||dialog;
  const rows=[],seen=new Set();
  for(const a of root.querySelectorAll('a[href]')){
    let url;try{url=new URL(a.href,location.href);}catch(_){continue;}
    const match=decodeURIComponent(url.pathname).match(/^\/@([A-Za-z0-9._~-]+)\/?$/); if(!match)continue;
    const username=match[1]; if(username.toLowerCase()===subject||seen.has(username.toLowerCase()))continue;
    seen.add(username.toLowerCase());
    let row=a;
    for(let node=a.parentElement;node&&node!==root;node=node.parentElement){const text=clean(node.innerText||'');if(text.length<700&&/\bFollow(?:ing)?\b/i.test(text)){row=node;break;}}
    const img=row.querySelector('img')||a.querySelector('img');
    rows.push({href:url.origin+'/@'+username,username,anchorText:clean(a.innerText||''),itemText:clean(row.innerText||'').slice(0,600),imageSrc:img?.currentSrc||img?.src||'',imageAlt:img?.alt||''});
  }
  const rr=root.getBoundingClientRect();
  return {ok:true,relation,url:location.href,records:rows,scrollTop:root.scrollTop||0,scrollHeight:root.scrollHeight||0,clientHeight:root.clientHeight||0,
    scrollX:rr.left+rr.width/2,scrollY:rr.top+rr.height/2,atEnd:(root.scrollTop||0)+(root.clientHeight||0)>=(root.scrollHeight||0)-3};
})()
"""

THREADS_SCROLL_JS = r"""
(() => {
  const dialog=[...document.querySelectorAll('[role="dialog"],[aria-modal="true"]')].find(el=>{const r=el.getBoundingClientRect(),t=(el.innerText||'');return r.width>0&&r.height>0&&/followers\s+[0-9]/i.test(t)&&/following\s+[0-9]/i.test(t);});
  if(!dialog)return {ok:false};
  const root=[...dialog.querySelectorAll('*')].filter(el=>{const s=getComputedStyle(el);return el.scrollHeight>el.clientHeight+8&&['auto','scroll'].includes(s.overflowY);}).sort((a,b)=>(b.clientWidth*b.clientHeight)-(a.clientWidth*a.clientHeight))[0]||dialog;
  const before=root.scrollTop;
  root.scrollTop=Math.min(root.scrollHeight,before+Math.max(1,Math.floor(root.clientHeight*.8)));root.dispatchEvent(new Event('scroll',{bubbles:true}));
  return {ok:true,before,after:root.scrollTop};
})()
"""


def tiktok_parse_exact_tab_count(text: str, relation: str) -> int | None:
    match = re.fullmatch(rf"\s*(?:{re.escape(relation)}\s+([0-9][0-9,.]*[kmb]?)|([0-9][0-9,.]*)\s+{re.escape(relation)})\s*", str(text or ""), re.I)
    return parse_count((match.group(1) or match.group(2))) if match else None


def tiktok_row_candidate(username: str, href: str, source_url: str, *, active_relation: str, row_in_modal: bool = True) -> str | None:
    if not row_in_modal or active_relation not in {"followers", "following"}:
        return None
    if "suggested" in str(username or "").casefold() or "suggested" in str(href or "").casefold():
        return None
    if href and normalize_profile_link("tiktok", href, source_url):
        return tiktok_canonical_url(username or href.rsplit("/", 1)[-1], source_url)
    return tiktok_canonical_url(username, source_url)


def relationship_payload_pages(platform: str, response_url: str, payload: Any, relation: str) -> list[dict[str, Any]]:
    """Summarize pagination only for the requested relationship response.

    This is deliberately narrow. Recommendation and friendship-status payloads
    must not be treated as proof that a relationship list reached its end.
    """
    lower_url = str(response_url or "").casefold()

    if platform == "threads":
        if relation not in {"followers", "following"} or "graphql" not in lower_url:
            return []
        pages: list[dict[str, Any]] = []

        def walk_threads(value: Any) -> None:
            if isinstance(value, dict):
                relationship = value.get(relation)
                if isinstance(relationship, dict) and isinstance(relationship.get("edges"), list):
                    edges = [edge for edge in relationship["edges"] if isinstance(edge, dict)]
                    page_info = relationship.get("page_info") if isinstance(relationship.get("page_info"), dict) else {}
                    pages.append({
                        "records": len(edges),
                        "has_more": page_info.get("has_next_page") if isinstance(page_info.get("has_next_page"), bool) else None,
                        "next_cursor": page_info.get("end_cursor"),
                        "page_size": len(edges),
                        "status": None,
                    })
                for child in value.values():
                    walk_threads(child)
            elif isinstance(value, list):
                for child in value:
                    walk_threads(child)

        walk_threads(payload)
        return pages

    if platform == "x":
        if relation not in {"followers", "following"} or "/i/api/graphql/" not in lower_url:
            return []
        operation = lower_url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
        if operation != relation:
            return []

        user_results = 0
        bottom_cursor_seen = False
        bottom_cursor: str | None = None

        def walk_x(value: Any) -> None:
            nonlocal user_results, bottom_cursor_seen, bottom_cursor
            if isinstance(value, dict):
                if isinstance(value.get("user_results"), dict):
                    user_results += 1
                content = value.get("content")
                if isinstance(content, dict) and str(content.get("cursorType") or "").casefold() == "bottom":
                    bottom_cursor_seen = True
                    raw_cursor = content.get("value")
                    bottom_cursor = str(raw_cursor) if raw_cursor not in {None, ""} else None
                for child in value.values():
                    walk_x(child)
            elif isinstance(value, list):
                for child in value:
                    walk_x(child)

        walk_x(payload)
        if not user_results and not bottom_cursor_seen:
            return []
        return [{
            "records": user_results,
            "has_more": bool(bottom_cursor) if bottom_cursor_seen else None,
            "next_cursor": bottom_cursor,
            "page_size": None,
            "status": None,
        }]

    if platform == "poshmark":
        if relation not in {"followers", "following"}:
            return []
        if not re.search(rf"/vm-rest/users/[^/]+/{re.escape(relation)}(?:\?|$)", lower_url):
            return []
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            return []
        more = payload.get("more") if isinstance(payload.get("more"), dict) else {}
        next_max_id = more.get("next_max_id")
        return [{
            "records": len(payload["data"]),
            "has_more": next_max_id not in {None, "", False},
            "next_max_id": next_max_id,
            "page_size": len(payload["data"]),
            "status": None,
        }]

    if platform != "instagram":
        return []
    if f"/friendships/" not in lower_url or f"/{relation.casefold()}/" not in lower_url:
        return []

    pages: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            users = value.get("users")
            if isinstance(users, list) and ("has_more" in value or "next_max_id" in value or "big_list" in value):
                pages.append({
                    "records": len(users),
                    "has_more": value.get("has_more") if isinstance(value.get("has_more"), bool) else None,
                    "next_max_id": value.get("next_max_id"),
                    "page_size": value.get("page_size"),
                    "status": value.get("status"),
                })
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return pages


def relationship_collection_policy(platform: str, settings: dict[str, Any]) -> dict[str, int | None]:
    """Return bounded per-platform retry/stability settings for one relation."""
    configured_retries = max(0, int(settings.get("completion_retry_limit", 1)))
    if platform in {"facebook", "instagram", "x"}:
        setting_name = f"{platform}_content_stall_round_limit"
        default_stall_limit = {
            "facebook": 12,
            "instagram": 20,
            "x": 18,
        }[platform]
        return {
            "completion_retry_limit": 0,
            "content_stall_round_limit": max(
                1, int(settings.get(setting_name, default_stall_limit))
            ),
        }
    if platform == "poshmark":
        # The rendered directory replays the same cursor sequence when reopened.
        # One complete pass captures every row the authenticated browser exposes.
        return {
            "completion_retry_limit": 0,
            "content_stall_round_limit": None,
        }
    return {
        "completion_retry_limit": configured_retries,
        "content_stall_round_limit": None,
    }


def trusted_exhausted_instagram_count_lag(
    platform: str,
    reported_count: int | None,
    records: list[dict[str, Any]],
    payload_exhausted: bool,
    payload_has_more: bool | None,
) -> bool:
    """Trust a one-row stale Instagram count only with dual-source proof."""
    return bool(
        platform == "instagram"
        and reported_count is not None
        and reported_count > 0
        and len(records) == reported_count + 1
        and payload_exhausted
        and payload_has_more is False
        and records
        and all(
            "visible_browser_dom" in str(record.get("extraction_source") or "")
            and "browser_network_response" in str(record.get("extraction_source") or "")
            for record in records
        )
    )


class BrowserCollector:
    def __init__(self, endpoint: str, settings: dict[str, Any]):
        self.browser = CDPBrowser(endpoint)
        self.settings = settings

    def check(self) -> dict[str, Any]:
        return self.browser.check()

    @staticmethod
    def _tiktok_trusted_click(tab: CDPTab, x: float, y: float) -> None:
        """Click a visible target in the active Chromium page, not a background tab."""
        tab.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        tab.call("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
        time.sleep(0.12)
        tab.call("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})

    @staticmethod
    def _tiktok_key_fallback(tab: CDPTab, key: str) -> None:
        tab.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": key, "code": key, "windowsVirtualKeyCode": 13 if key == "Enter" else 32})
        tab.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": key, "code": key, "windowsVirtualKeyCode": 13 if key == "Enter" else 32})

    @staticmethod
    def _x_trusted_scroll(tab: CDPTab, x: float, y: float, client_height: int) -> int:
        """Advance X's visible relationship timeline with a real, gradual wheel event."""
        delta_y = max(240, min(720, int(max(1, client_height) * 0.8)))
        tab.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        tab.call("Input.dispatchMouseEvent", {
            "type": "mouseWheel",
            "x": x,
            "y": y,
            "deltaX": 0,
            "deltaY": delta_y,
        })
        return delta_y

    @staticmethod
    def _tiktok_status(reported: int | None, collected: int, reason: str) -> str:
        if reported is not None and collected > reported:
            return "review"
        if reported is not None and collected == reported:
            return "complete"
        if collected:
            return "incomplete"
        return "failed"

    def _collect_tiktok(
        self, source_url: str, relation: str, diagnostics_dir: Path, checkpoint_path: Path
    ) -> CollectionOutcome:
        started = utc_now()
        if relation not in {"followers", "following"}:
            return CollectionOutcome("tiktok", source_url, relation, None, None, 0, "failed", "unsupported_tiktok_relation", started, utc_now(), [], {})
        tab = self.browser.new_tab("about:blank")
        records_by_key: dict[str, dict[str, Any]] = {}
        reported = None
        reported_raw = None
        reason = "unknown"
        diagnostics: dict[str, Any] = {"dedicated_adapter": True, "navigation_events": [], "clicked_controls": []}
        opened_from_profile = False
        try:
            settle = float(self.settings.get("settle_seconds", 3.0))
            render_settle = max(settle, 4.0)
            print(f"  [tiktok:{relation}] opening source profile {source_url}")
            tab.navigate(source_url, render_settle)
            source_canonical = source_url.rstrip("/")
            if tab.current_url().rstrip("/") != source_canonical:
                return CollectionOutcome("tiktok", source_url, relation, None, None, 0, "failed", "source_profile_navigation_failed", started, utc_now(), [], diagnostics)
            unavailable_state = tab.evaluate(TIKTOK_PROFILE_UNAVAILABLE_STATE_JS) or {}
            if unavailable_state.get("unavailable"):
                reason = "source_profile_unavailable"
                diagnostics.update({
                    "source_profile_unavailable": True,
                    "evidence": unavailable_state.get("evidence"),
                    "current_url": tab.current_url(),
                    "title": unavailable_state.get("title") or tab.title(),
                })
                diagnostics_dir.mkdir(parents=True, exist_ok=True)
                tab.screenshot(diagnostics_dir / f"{relation}-unavailable.png")
                tab.save_html(diagnostics_dir / f"{relation}-unavailable.html")
                write_json(diagnostics_dir / f"{relation}-unavailable.json", diagnostics)
                return CollectionOutcome(
                    "tiktok", source_url, relation, None, None, 0,
                    "blocked", reason, started, utc_now(), [], diagnostics,
                )
            # TikTok hydrates and repositions the profile header after the initial
            # document becomes interactive. Do not measure a moving stat target.
            time.sleep(0.25)
            tab.call("Page.bringToFront")
            time.sleep(0.25)
            modal_expr = TIKTOK_MODAL_JS.replace("__RELATION__", json.dumps(relation)).replace("__SOURCE__", json.dumps(source_url))
            modal = tab.evaluate(modal_expr) or {}
            profile_reported = (
                tiktok_parse_exact_tab_count(str(modal.get("clickedText") or ""), relation)
                if modal.get("opening")
                else None
            )
            # TikTok can transiently hydrate a real profile as 0/0 after several
            # consecutive modal requests. Confirm a zero profile count on fresh
            # page loads before accepting it as an exact empty relationship.
            zero_count_profile_retries = 0
            while modal.get("opening") and profile_reported == 0 and zero_count_profile_retries < 2:
                zero_count_profile_retries += 1
                tab.navigate(source_url, render_settle)
                tab.call("Page.bringToFront")
                time.sleep(0.5)
                modal = tab.evaluate(modal_expr) or {}
                refreshed_count = (
                    tiktok_parse_exact_tab_count(str(modal.get("clickedText") or ""), relation)
                    if modal.get("opening")
                    else None
                )
                if refreshed_count is not None:
                    profile_reported = refreshed_count
            diagnostics["zero_count_profile_retries"] = zero_count_profile_retries
            reported = profile_reported if profile_reported is not None else modal.get("reported")
            reported_raw = str(profile_reported) if profile_reported is not None else modal.get("reportedRaw")
            diagnostics["modal"] = modal
            profile_private_state = tab.evaluate(TIKTOK_PROFILE_PRIVATE_STATE_JS) or {}
            diagnostics["profile_private_state"] = profile_private_state
            # A zero profile stat, confirmed across the bounded fresh-page
            # retries above, is an exact empty relationship. TikTok does not
            # open a modal for an empty list, so do not treat that as failure.
            if reported == 0:
                reason = "collected_unique_equals_reported_count"
                diagnostics["exact_zero_count"] = True
                write_json(checkpoint_path, {
                    "platform": "tiktok",
                    "source_profile_url": source_url,
                    "relation": relation,
                    "reported_count": 0,
                    "collected_this_run": 0,
                    "updated_at": utc_now(),
                    "records": [],
                })
                return CollectionOutcome(
                    "tiktok", source_url, relation, 0, reported_raw, 0,
                    "complete", reason, started, utc_now(), [], diagnostics,
                )
            if modal.get("opening"):
                opened_from_profile = True
                strategies = [("pointer_center", None), ("pointer_interior", None), ("keyboard_fallback", None)]
                modal_verified = False
                for strategy, key in strategies:
                    diagnostics.setdefault("click_strategies", []).append(strategy)
                    print(f"  [tiktok:{relation}] trusted click strategy={strategy}")
                    # Re-measure immediately before each strategy; TikTok can finish
                    # a header layout pass after the first DOM scan.
                    time.sleep(0.35)
                    refreshed = tab.evaluate(modal_expr) or {}
                    if refreshed.get("opening"):
                        modal = refreshed
                        refreshed_count = tiktok_parse_exact_tab_count(
                            str(refreshed.get("clickedText") or ""), relation
                        )
                        if refreshed_count is not None and (
                            profile_reported is None or refreshed_count > 0
                        ):
                            profile_reported = refreshed_count
                            reported, reported_raw = refreshed_count, str(refreshed_count)
                    if strategy == "pointer_center":
                        self._tiktok_trusted_click(tab, float(modal.get("x", 0)), float(modal.get("y", 0)))
                    elif strategy == "pointer_interior":
                        self._tiktok_trusted_click(tab, float(modal.get("ix", modal.get("x", 0))), float(modal.get("iy", modal.get("y", 0))))
                    else:
                        self._tiktok_key_fallback(tab, "Enter")
                        time.sleep(0.25)
                        self._tiktok_key_fallback(tab, "Space")
                    time.sleep(max(settle, 3.5))
                    candidate = tab.evaluate(modal_expr) or {}
                    diagnostics.setdefault("after_clicks", []).append({"strategy": strategy, "url": tab.current_url(), "modal": candidate})
                    diagnostics_dir.mkdir(parents=True, exist_ok=True)
                    tab.screenshot(diagnostics_dir / f"{relation}-{strategy}.png")
                    tab.save_html(diagnostics_dir / f"{relation}-{strategy}.html")
                    if candidate.get("ok"):
                        modal = candidate
                        modal_reported = modal.get("reported")
                        if (
                            profile_reported is not None
                            and modal_reported is not None
                            and modal_reported != profile_reported
                        ):
                            reason = "tiktok_profile_modal_count_mismatch"
                            diagnostics["profile_reported_count"] = profile_reported
                            diagnostics["modal_reported_count"] = modal_reported
                            diagnostics["modal_count_mismatch"] = True
                            return CollectionOutcome(
                                "tiktok", source_url, relation, profile_reported,
                                str(profile_reported), 0, "failed", reason,
                                started, utc_now(), [], diagnostics,
                            )
                        reported = profile_reported if profile_reported is not None else modal_reported
                        reported_raw = str(reported) if reported is not None else modal.get("reportedRaw")
                        modal_verified = True
                        break
                    modal = candidate or modal
                if not modal_verified:
                    if profile_private_state.get("private"):
                        reason = "private_profile_relationship_list_unavailable"
                        diagnostics.update({
                            "private_profile": True,
                            "private_relation": relation,
                            "current_url": tab.current_url(),
                            "displayed_count_gap": reported,
                        })
                        diagnostics_dir.mkdir(parents=True, exist_ok=True)
                        tab.screenshot(diagnostics_dir / f"{relation}-private.png")
                        tab.save_html(diagnostics_dir / f"{relation}-private.html")
                        write_json(diagnostics_dir / f"{relation}-private.json", diagnostics)
                        return CollectionOutcome(
                            "tiktok", source_url, relation, reported, reported_raw, 0,
                            "private", reason, started, utc_now(), [], diagnostics,
                        )
                    reason = str(modal.get("reason") or "relationship_modal_not_found")
                    diagnostics["final_failure_reason"] = reason
                    diagnostics_dir.mkdir(parents=True, exist_ok=True)
                    tab.screenshot(diagnostics_dir / f"{relation}.png")
                    tab.save_html(diagnostics_dir / f"{relation}.html")
                    write_json(diagnostics_dir / f"{relation}.json", diagnostics)
                    return CollectionOutcome("tiktok", source_url, relation, reported, reported_raw, 0, "failed", reason, started, utc_now(), [], diagnostics)
            if not modal.get("ok"):
                return CollectionOutcome("tiktok", source_url, relation, reported, reported_raw, 0, "failed", str(modal.get("reason") or "relationship_modal_not_found"), started, utc_now(), [], diagnostics)
            diagnostics["clicked_controls"].append(relation)
            diagnostics["modal_verified"] = modal
            print(f"  [tiktok:{relation}] modal verified; active relation={relation}; expected={reported}")
            # The exact source-profile stat opens the requested relation tab. Do not
            # run a second broad modal control scan: its descendants can be row links.
            diagnostics["active_tab_verification"] = "source_profile_stat_opened_requested_relation" if opened_from_profile else modal.get("active")
            if not opened_from_profile:
                self._tiktok_trusted_click(tab, float(modal.get("x", 0)), float(modal.get("y", 0)))
                time.sleep(max(settle, 1.0))
            if modal.get("privateList"):
                reason = "private_profile_relationship_list_unavailable"
                diagnostics.update({
                    "private_profile": True,
                    "private_relation": relation,
                    "current_url": tab.current_url(),
                    "displayed_count_gap": reported,
                })
                diagnostics_dir.mkdir(parents=True, exist_ok=True)
                tab.screenshot(diagnostics_dir / f"{relation}-private.png")
                tab.save_html(diagnostics_dir / f"{relation}-private.html")
                write_json(diagnostics_dir / f"{relation}-private.json", diagnostics)
                return CollectionOutcome(
                    "tiktok", source_url, relation, reported, reported_raw, 0,
                    "private", reason, started, utc_now(), [], diagnostics,
                )
            max_rounds = int(self.settings.get("max_scroll_rounds", 1000))
            delay = float(self.settings.get("scroll_delay_seconds", 1.0))
            stable_rounds = 0
            no_progress_rounds = 0
            last_signature = None
            for round_no in range(1, max_rounds + 1):
                if tab.current_url().rstrip("/") != source_canonical:
                    diagnostics["navigation_events"].append(tab.current_url())
                    tab.navigate(source_url, settle)
                    reason = "navigation_guard_triggered"
                    break
                state = tab.evaluate(TIKTOK_LIST_STATE_JS.replace("__RELATION__", json.dumps(relation)).replace("__SOURCE__", json.dumps(source_url))) or {}
                if not state.get("ok"):
                    reason = str(state.get("reason") or "tiktok_list_unavailable")
                    break
                # The scan happens before every scroll; virtualized rows are accumulated in memory.
                before = len(records_by_key)
                visible_usernames = [str(raw.get("username") or "") for raw in state.get("records") or []]
                for raw in state.get("records") or []:
                    if reported is not None and len(records_by_key) >= reported:
                        break
                    username = str(raw.get("username") or "")
                    canonical = tiktok_canonical_url(username, source_url)
                    if not canonical or canonical.casefold() in records_by_key:
                        continue
                    records_by_key[canonical.casefold()] = {
                        "platform": "tiktok", "relationship": relation, "source_profile_url": source_url,
                        "username": username.lstrip("@"), "display_name": None, "profile_url": canonical,
                        "platform_user_id": None, "avatar_url": None, "collected_at": utc_now(),
                        "extraction_source": "visible_browser_dom",
                    }
                new_count = len(records_by_key) - before
                print(
                    f"\r  [tiktok:{relation}] round={round_no} visible={len(visible_usernames)} "
                    f"new={new_count} accumulated={len(records_by_key)} "
                    f"first={visible_usernames[0] if visible_usernames else '-'} "
                    f"last={visible_usernames[-1] if visible_usernames else '-'} "
                    f"scroll={state.get('scrollTop', 0)}/{state.get('scrollHeight', 0)} "
                    f"viewport={state.get('clientHeight', 0)}      ",
                    end="",
                    flush=True,
                )
                write_json(checkpoint_path, {"platform":"tiktok", "source_profile_url":source_url, "relation":relation, "reported_count":reported, "collected_this_run":len(records_by_key), "round":round_no, "records":list(records_by_key.values()), "updated_at":utc_now()})
                if reported is not None and len(records_by_key) == reported:
                    reason = "collected_unique_equals_reported_count"
                    break
                signature = (len(records_by_key), state.get("scrollTop"), state.get("scrollHeight"), tuple(sorted(r.get("username") for r in state.get("records") or [])))
                if state.get("atEnd") and signature == last_signature:
                    stable_rounds += 1
                else:
                    stable_rounds = 0
                last_signature = signature
                if stable_rounds >= 2:
                    reason = "modal_reached_stable_end"
                    break
                scroll_action = tab.evaluate(TIKTOK_SCROLL_JS) or {}
                if (
                    new_count == 0
                    and (
                        not scroll_action.get("ok")
                        or scroll_action.get("after") == scroll_action.get("before")
                    )
                ):
                    no_progress_rounds += 1
                else:
                    no_progress_rounds = 0
                if no_progress_rounds >= 3:
                    reason = "modal_reached_stable_end"
                    break
                time.sleep(delay)
            else:
                reason = "maximum_scroll_rounds_reached"
            print()
            records = sorted(records_by_key.values(), key=lambda x: x["profile_url"])
            collected = len(records)
            status = self._tiktok_status(reported, collected, reason)
            diagnostics.update({
                "current_url": tab.current_url(),
                "rounds": round_no if 'round_no' in locals() else 0,
                "scroll_container_only": True,
                "suggested_ignored": True,
                "network_capture_used": False,
                "list_passes": 1,
                "displayed_count_gap": (
                    max(0, reported - collected) if reported is not None else None
                ),
            })
            return CollectionOutcome("tiktok", source_url, relation, reported, reported_raw, collected, status, reason, started, utc_now(), records, diagnostics)
        except Exception as exc:
            return CollectionOutcome("tiktok", source_url, relation, reported, reported_raw, len(records_by_key), "failed", f"{type(exc).__name__}: {exc}", started, utc_now(), list(records_by_key.values()), diagnostics)
        finally:
            try:
                if tab.current_url().rstrip("/") == source_url.rstrip("/"):
                    tab.evaluate("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',code:'Escape',bubbles:true})); delete window.__contactAnalyzerTikTokModalRelation")
                else:
                    tab.navigate(source_url, float(self.settings.get("settle_seconds", 3.0)))
            except Exception:
                pass
            tab.close(close_target=True)

    def _collect_threads_pass(
        self, source_url: str, relation: str, diagnostics_dir: Path, checkpoint_path: Path
    ) -> CollectionOutcome:
        started = utc_now()
        if relation not in {"followers", "following"}:
            return CollectionOutcome("threads", source_url, relation, None, None, 0, "failed", "unsupported_threads_relation", started, utc_now(), [], {})
        tab = self.browser.new_tab("about:blank")
        records_by_key: dict[str, dict[str, Any]] = {}
        reported: int | None = None
        reported_raw: str | None = None
        reason = "unknown"
        diagnostics: dict[str, Any] = {
            "dedicated_adapter": True,
            "navigation_events": [],
            "clicked_controls": [],
            "follow_buttons_clicked": 0,
            "network_payloads": [],
            "scroll_event_errors": [],
        }
        try:
            settle = max(float(self.settings.get("settle_seconds", 3.0)), 4.0)
            source_canonical = source_url.rstrip("/")
            print(f"  [threads:{relation}] opening source profile {source_url}")
            tab.navigate(source_url, settle)
            tab.call("Page.bringToFront")
            profile_state = tab.evaluate(THREADS_PROFILE_STATE_JS) or {}
            diagnostics["profile_state"] = profile_state
            profile = tab.evaluate(THREADS_PROFILE_CONTROL_JS.replace("__SOURCE__", json.dumps(source_url))) or {}
            diagnostics["profile_control"] = profile
            if not profile.get("ok"):
                if relation == "followers" and profile_state.get("followers") == 0:
                    write_json(checkpoint_path, {
                        "platform": "threads",
                        "source_profile_url": source_url,
                        "relation": relation,
                        "reported_count": 0,
                        "collected_this_run": 0,
                        "updated_at": utc_now(),
                        "records": [],
                    })
                    return CollectionOutcome(
                        "threads", source_url, relation, 0,
                        str(profile_state.get("followersRaw") or "0"), 0,
                        "complete", "collected_unique_equals_reported_count",
                        started, utc_now(), [], diagnostics,
                    )
                if profile_state.get("private"):
                    reported = (
                        profile_state.get("followers")
                        if relation == "followers"
                        else None
                    )
                    reported_raw = (
                        str(profile_state.get("followersRaw") or "") or None
                        if relation == "followers"
                        else None
                    )
                    reason = "private_profile_relationship_list_unavailable"
                    diagnostics_dir.mkdir(parents=True, exist_ok=True)
                    tab.screenshot(diagnostics_dir / f"{relation}-private.png")
                    tab.save_html(diagnostics_dir / f"{relation}-private.html")
                    write_json(diagnostics_dir / f"{relation}-private.json", diagnostics)
                    return CollectionOutcome(
                        "threads", source_url, relation, reported, reported_raw, 0,
                        "private", reason, started, utc_now(), [], diagnostics,
                    )
                reason = str(profile.get("reason") or "exact_threads_followers_control_not_found")
                return CollectionOutcome("threads", source_url, relation, None, None, 0, "failed", reason, started, utc_now(), [], diagnostics)

            tab.clear_network_capture()
            print(f"  [threads:{relation}] trusted click strategy=pointer_center")
            self._tiktok_trusted_click(tab, float(profile.get("x") or 0), float(profile.get("y") or 0))
            time.sleep(settle)
            modal = tab.evaluate(
                THREADS_MODAL_JS.replace("__RELATION__", json.dumps(relation)).replace("__SOURCE__", json.dumps(source_url))
            ) or {}
            if not modal.get("ok"):
                print(f"  [threads:{relation}] pointer click did not verify modal; trying focused Enter")
                refreshed = tab.evaluate(THREADS_PROFILE_CONTROL_JS.replace("__SOURCE__", json.dumps(source_url))) or profile
                self._tiktok_key_fallback(tab, "Enter")
                time.sleep(settle)
                modal = tab.evaluate(
                    THREADS_MODAL_JS.replace("__RELATION__", json.dumps(relation)).replace("__SOURCE__", json.dumps(source_url))
                ) or {}
                diagnostics["keyboard_profile_control"] = refreshed
            if not modal.get("ok"):
                reason = str(modal.get("reason") or "threads_relationship_modal_not_found")
                diagnostics["modal_failure"] = modal
                diagnostics_dir.mkdir(parents=True, exist_ok=True)
                tab.screenshot(diagnostics_dir / f"{relation}.png")
                tab.save_html(diagnostics_dir / f"{relation}.html")
                write_json(diagnostics_dir / f"{relation}.json", diagnostics)
                return CollectionOutcome("threads", source_url, relation, None, None, 0, "failed", reason, started, utc_now(), [], diagnostics)

            diagnostics["clicked_controls"].append("followers")
            if modal.get("active") != relation:
                tab.clear_network_capture()
                print(f"  [threads:{relation}] switching exact modal tab to {relation}")
                self._tiktok_trusted_click(tab, float(modal.get("x") or 0), float(modal.get("y") or 0))
                diagnostics["clicked_controls"].append(relation)
                time.sleep(settle)
                modal = tab.evaluate(
                    THREADS_MODAL_JS.replace("__RELATION__", json.dumps(relation)).replace("__SOURCE__", json.dumps(source_url))
                ) or {}
            if not modal.get("ok") or modal.get("active") != relation:
                reason = "threads_requested_tab_not_active"
                diagnostics["modal_failure"] = modal
                return CollectionOutcome("threads", source_url, relation, modal.get("reported"), modal.get("reportedRaw"), 0, "failed", reason, started, utc_now(), [], diagnostics)

            reported = modal.get("reported")
            reported_raw = modal.get("reportedRaw")
            diagnostics["modal_verified"] = modal
            print(f"  [threads:{relation}] modal verified; active relation={relation}; expected={reported}")
            max_rounds = min(1000, int(self.settings.get("max_scroll_rounds", 1000)))
            delay = max(0.35, float(self.settings.get("scroll_delay_seconds", 1.0)))
            stable_rounds = 0
            idle_rounds = 0
            last_signature: tuple[Any, ...] | None = None
            relationship_has_more: bool | None = None
            relationship_payload_seen = False
            content_stall_limit = max(
                6,
                min(
                    30,
                    int(self.settings.get("threads_content_stall_round_limit", 30)),
                ),
            )
            for round_no in range(1, max_rounds + 1):
                if tab.current_url().rstrip("/") != source_canonical:
                    diagnostics["navigation_events"].append(tab.current_url())
                    reason = "navigation_guard_triggered"
                    records_by_key.clear()
                    break
                state = tab.evaluate(
                    THREADS_LIST_STATE_JS.replace("__RELATION__", json.dumps(relation)).replace("__SOURCE__", json.dumps(source_url))
                ) or {}
                if not state.get("ok"):
                    reason = str(state.get("reason") or "threads_list_unavailable")
                    break
                before = len(records_by_key)
                for record in self._dom_records("threads", source_url, relation, state):
                    self._merge_record(records_by_key, record)

                network_pages_this_round = 0
                for response_url, payload in tab.drain_json_responses(("graphql",)):
                    pages = relationship_payload_pages("threads", response_url, payload, relation)
                    if not pages:
                        continue
                    relationship_payload_seen = True
                    network_pages_this_round += len(pages)
                    network_records = self._network_candidates("threads", payload, source_url, relation)
                    diagnostics["network_payloads"].extend({
                        **page,
                        "url": response_url,
                        "round": round_no,
                        "candidate_count": len(network_records),
                        "candidate_usernames": [record.get("username") for record in network_records],
                    } for page in pages)
                    for page in pages:
                        if page.get("has_more") is not None:
                            relationship_has_more = bool(page.get("has_more"))
                    for record in network_records:
                        record.update({"relationship": relation, "source_profile_url": source_url, "collected_at": utc_now()})
                        self._merge_record(records_by_key, record)

                added = len(records_by_key) - before
                visible = [str(item.get("username") or "") for item in state.get("records") or []]
                signature = (len(records_by_key), state.get("scrollTop"), state.get("scrollHeight"), tuple(visible))
                changed = bool(added or network_pages_this_round or signature != last_signature)
                idle_rounds = 0 if changed else idle_rounds + 1
                if changed or round_no == 1 or idle_rounds % 10 == 0:
                    print(
                        f"\r  [threads:{relation}] round={round_no} visible={len(visible)} "
                        f"new={added} accumulated={len(records_by_key)}/{reported if reported is not None else '?'} "
                        f"first={visible[0] if visible else '-'} last={visible[-1] if visible else '-'} "
                        f"scroll={state.get('scrollTop', 0)}/{state.get('scrollHeight', 0)} viewport={state.get('clientHeight', 0)}      ",
                        end="", flush=True,
                    )
                write_json(checkpoint_path, {
                    "platform": "threads", "source_profile_url": source_url, "relation": relation,
                    "reported_count": reported, "collected_this_run": len(records_by_key), "round": round_no,
                    "records": list(records_by_key.values()), "updated_at": utc_now(),
                })
                if reported is not None and len(records_by_key) >= reported:
                    reason = "collected_unique_equals_reported_count" if len(records_by_key) == reported else "collected_more_than_reported_count"
                    break
                if state.get("atEnd") and signature == last_signature and (
                    relationship_has_more is False
                    or (not relationship_payload_seen and idle_rounds >= 10)
                ):
                    stable_rounds += 1
                else:
                    stable_rounds = 0
                last_signature = signature
                if stable_rounds >= 2:
                    reason = (
                        "platform_relationship_payload_exhausted_before_displayed_count"
                        if relationship_has_more is False and reported is not None and len(records_by_key) < reported
                        else "modal_reached_stable_end"
                    )
                    break
                if (
                    state.get("atEnd")
                    and relationship_has_more is True
                    and idle_rounds >= content_stall_limit
                ):
                    reason = "browser_relationship_cursor_not_requested"
                    break
                # One modal-scoped scroll starts one visible-list load. Threads can
                # stop acknowledging CDP mouseWheel events while its cursor request
                # is throttled, so use the verified inner container directly. While
                # that request is pending, wait instead of queuing duplicate calls.
                if not state.get("atEnd") or changed or idle_rounds % 10 == 0:
                    diagnostics["scroll_strategy"] = "modal_scoped_dom_scroll"
                    scroll_result = tab.evaluate(THREADS_SCROLL_JS) or {}
                    if not scroll_result.get("ok"):
                        reason = "browser_relationship_cursor_not_requested"
                        break
                time.sleep(delay)
            else:
                reason = "maximum_scroll_rounds_reached"
            print()
            records = sorted(records_by_key.values(), key=lambda item: item["profile_url"].casefold())
            collected = len(records)
            status = self._tiktok_status(reported, collected, reason)
            diagnostics.update({
                "current_url": tab.current_url(), "rounds": round_no if "round_no" in locals() else 0,
                "scroll_container_only": True, "network_capture_used": True,
                "relationship_has_more": relationship_has_more,
                "relationship_payload_seen": relationship_payload_seen,
                "idle_rounds": idle_rounds,
                "displayed_count_gap": max(0, reported - collected) if reported is not None else None,
            })
            if status != "complete":
                diagnostics_dir.mkdir(parents=True, exist_ok=True)
                tab.screenshot(diagnostics_dir / f"{relation}.png")
                tab.save_html(diagnostics_dir / f"{relation}.html")
                write_json(diagnostics_dir / f"{relation}.json", diagnostics)
            return CollectionOutcome("threads", source_url, relation, reported, reported_raw, collected, status, reason, started, utc_now(), records, diagnostics)
        except Exception as exc:
            return CollectionOutcome("threads", source_url, relation, reported, reported_raw, len(records_by_key), "failed", f"{type(exc).__name__}: {exc}", started, utc_now(), list(records_by_key.values()), diagnostics)
        finally:
            try:
                if tab.current_url().rstrip("/") == source_url.rstrip("/"):
                    tab.evaluate("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',code:'Escape',bubbles:true}))")
                else:
                    tab.navigate(source_url, float(self.settings.get("settle_seconds", 3.0)))
            except Exception:
                pass
            tab.close(close_target=True)

    def _collect_threads(
        self, source_url: str, relation: str, diagnostics_dir: Path, checkpoint_path: Path
    ) -> CollectionOutcome:
        """Accumulate bounded, independent Threads modal passes in one run.

        Threads frequently exposes a different subset of a virtualized
        relationship list each time the exact modal is opened.  Each pass still
        uses the dedicated modal-only collector; this wrapper only unions trusted
        canonical rows and stops at the exact count or after repeated no-growth
        passes.
        """
        started = utc_now()
        if relation not in {"followers", "following"}:
            return CollectionOutcome(
                "threads", source_url, relation, None, None, 0, "failed",
                "unsupported_threads_relation", started, utc_now(), [], {},
            )

        pass_limit = max(1, int(self.settings.get("threads_completion_pass_limit", 6)))
        no_new_limit = max(1, int(self.settings.get("threads_no_new_pass_limit", 2)))
        records_by_url: dict[str, dict[str, Any]] = {}
        pass_summaries: list[dict[str, Any]] = []
        reported: int | None = None
        reported_raw: str | None = None
        no_new_passes = 0
        final_status = "failed"
        final_reason = "threads_relationship_pass_not_started"
        trusted_passes = 0

        for pass_number in range(1, pass_limit + 1):
            if pass_number > 1:
                print(
                    f"[threads:{relation}] exact-count retry {pass_number - 1}/{pass_limit - 1}: "
                    f"{len(records_by_url)}/{reported if reported is not None else '?'}",
                    flush=True,
                )
            pass_diagnostics = diagnostics_dir / f"pass-{pass_number}"
            pass_checkpoint = checkpoint_path.with_name(
                f"{relation}.pass-{pass_number}.checkpoint.json"
            )
            outcome = self._collect_threads_pass(
                source_url, relation, pass_diagnostics, pass_checkpoint
            )

            if outcome.reported_count is not None:
                reported = int(outcome.reported_count)
                reported_raw = outcome.reported_count_raw

            if outcome.status in {"failed", "blocked", "review", "private"}:
                pass_summaries.append({
                    "pass": pass_number,
                    "status": outcome.status,
                    "reason": outcome.reason,
                    "reported_count": outcome.reported_count,
                    "collected": outcome.collected_this_run,
                    "new_unique": 0,
                    "accumulated_unique": len(records_by_url),
                })
                if not trusted_passes:
                    final_status = outcome.status
                    final_reason = outcome.reason
                break

            trusted_passes += 1
            before = len(records_by_url)
            for record in outcome.records:
                self._merge_record(records_by_url, record)
            added = len(records_by_url) - before
            no_new_passes = 0 if added else no_new_passes + 1
            final_status = outcome.status
            final_reason = outcome.reason
            pass_summaries.append({
                "pass": pass_number,
                "status": outcome.status,
                "reason": outcome.reason,
                "reported_count": outcome.reported_count,
                "collected": outcome.collected_this_run,
                "new_unique": added,
                "accumulated_unique": len(records_by_url),
            })
            print(
                f"[threads:{relation}] aggregate pass={pass_number} new={added} "
                f"accumulated={len(records_by_url)}/"
                f"{reported if reported is not None else '?'}",
                flush=True,
            )
            write_json(checkpoint_path, {
                "platform": "threads",
                "source_profile_url": source_url,
                "relation": relation,
                "reported_count": reported,
                "collected_this_run": len(records_by_url),
                "list_pass": pass_number,
                "records": list(records_by_url.values()),
                "updated_at": utc_now(),
            })

            if reported is not None and len(records_by_url) == reported:
                final_status = "complete"
                final_reason = "collected_unique_equals_reported_count"
                break
            if reported is not None and len(records_by_url) > reported:
                final_status = "review"
                final_reason = "collected_more_than_reported_count"
                break
            if no_new_passes >= no_new_limit:
                final_status = "incomplete" if records_by_url else "failed"
                break

        records = sorted(
            records_by_url.values(), key=lambda item: str(item["profile_url"]).casefold()
        )
        if trusted_passes and final_status not in {"complete", "review"}:
            final_status = "incomplete" if records else "failed"
        diagnostics = {
            "dedicated_adapter": True,
            "list_passes": len(pass_summaries),
            "trusted_list_passes": trusted_passes,
            "pass_limit": pass_limit,
            "no_new_pass_limit": no_new_limit,
            "pass_summaries": pass_summaries,
            "reported_count": reported,
            "collected_unique": len(records),
            "displayed_count_gap": (
                max(0, reported - len(records)) if reported is not None else None
            ),
        }
        if final_status != "complete":
            diagnostics_dir.mkdir(parents=True, exist_ok=True)
            write_json(diagnostics_dir / f"{relation}-aggregate.json", diagnostics)
        return CollectionOutcome(
            "threads", source_url, relation, reported, reported_raw, len(records),
            final_status, final_reason, started, utc_now(), records, diagnostics,
        )

    def _count(self, tab: CDPTab, platform: str, source_url: str, relation: str) -> tuple[int | None, str | None, list[dict[str, Any]]]:
        if platform == "quora":
            if relation != "followers":
                return None, None, []
            state = tab.evaluate(
                QUORA_FOLLOWER_COUNT_JS.replace("__SOURCE__", json.dumps(source_url))
            ) or {}
            if state.get("ok") and state.get("count") is not None:
                candidate = {
                    "href": relation_url(platform, source_url, relation),
                    "text": state.get("text"),
                    "score": 250,
                    "source": "source_scoped_quora_follower_tab",
                }
                return int(state["count"]), str(state.get("raw") or state["count"]), [candidate]
            return None, None, [{"source": "source_scoped_quora_follower_tab", **state}]
        fragments = count_href_fragments(platform, source_url, relation)
        spec = SPECS.get(platform, SPECS["generic"])
        expression = (
            COUNT_SCAN_JS
            .replace("__FRAGMENTS__", json.dumps(fragments))
            .replace("__LABELS__", json.dumps(RELATION_LABELS[relation]))
            .replace("__CONTROL_SELECTORS__", json.dumps(spec.control_selectors))
        )
        candidates = tab.evaluate(expression) or []
        best_count: int | None = None
        best_raw: str | None = None
        labels = "|".join(re.escape(x) for x in RELATION_LABELS[relation])
        patterns = [
            re.compile(rf"(\d[\d,.\s]*[kmb]?)\s*(?:{labels})", re.I),
            re.compile(rf"(?:{labels})\s*(\d[\d,.\s]*[kmb]?)", re.I),
        ]
        for candidate in candidates:
            text = candidate.get("text", "")
            for pattern in patterns:
                match = pattern.search(text or "")
                if not match:
                    continue
                raw = match.group(1)
                parsed = parse_count(raw)
                # Compact K/M/B labels are estimates, not exact completion
                # targets. Keep the rendered label for diagnostics, but collect
                # to the stable end instead of truncating at the rounded value.
                if re.search(r"[kmb]", raw, re.I):
                    if best_raw is None:
                        best_raw = raw
                    continue
                if parsed is not None and (best_count is None or candidate.get("score", 0) > 100):
                    best_count, best_raw = parsed, raw
                    if candidate.get("score", 0) >= 200:
                        return best_count, best_raw, candidates
        if platform == "facebook" and relation == "friends" and best_count is None:
            context = tab.evaluate(FACEBOOK_FRIEND_COUNT_JS.replace("__SOURCE__", json.dumps(source_url))) or {}
            raw = str(context.get("raw") or "")
            parsed = parse_count(raw)
            if parsed is not None:
                candidates.append({"href": relation_url(platform, source_url, relation), "text": context.get("text"), "score": 180, "source": "facebook_friends_card"})
                return parsed, raw, candidates
        if platform == "pinterest" and best_count is None:
            # Empty Pinterest relationships are rendered as plain, non-clickable
            # profile-header text (for example, "0 following").  The normal
            # control scan intentionally ignores arbitrary divs, so verify this
            # narrowly scoped exact-zero state before declaring the control absent.
            state = tab.evaluate(
                PINTEREST_ZERO_COUNT_JS.replace(
                    "__LABELS__", json.dumps(RELATION_LABELS[relation])
                )
            ) or {}
            if state.get("ok") and state.get("count") == 0:
                candidate = {
                    "href": "",
                    "text": state.get("text"),
                    "score": 250,
                    "source": "pinterest_visible_zero_count",
                }
                candidates.append(candidate)
                return 0, str(state.get("raw") or "0"), candidates
        return best_count, best_raw, candidates

    def _count_with_hydration_retry(
        self,
        tab: CDPTab,
        platform: str,
        source_url: str,
        relation: str,
        settle_seconds: float,
    ) -> tuple[int | None, str | None, list[dict[str, Any]], int]:
        """Wait for Pinterest's delayed profile header before declaring it absent."""
        count, raw, candidates = self._count(tab, platform, source_url, relation)
        retries = 0
        if platform != "pinterest" or count is not None:
            return count, raw, candidates, retries

        for retry in range(1, 4):
            retries = retry
            if retry == 3:
                tab.navigate(source_url, max(settle_seconds, 6.0))
            else:
                time.sleep(2.0)
            retry_count, retry_raw, retry_candidates = self._count(
                tab, platform, source_url, relation
            )
            for candidate in retry_candidates:
                candidate = dict(candidate)
                candidate["hydration_retry"] = retry
                candidates.append(candidate)
            if retry_count is not None:
                return retry_count, retry_raw, candidates, retries
        return count, raw, candidates, retries

    def _click_relation(self, tab: CDPTab, platform: str, source_url: str, relation: str) -> dict[str, Any]:
        spec = SPECS.get(platform, SPECS["generic"])
        if platform in {"depop", "pinterest"}:
            target = tab.evaluate(
                TRUSTED_RELATION_TARGET_JS
                .replace("__LABELS__", json.dumps(RELATION_LABELS[relation]))
                .replace("__CONTROL_SELECTORS__", json.dumps(spec.control_selectors))
            ) or {"found": False}
            if not target.get("found"):
                return {"clicked": False, "method": "trusted_pointer", **target}
            self._tiktok_trusted_click(
                tab,
                float(target.get("x") or 1),
                float(target.get("y") or 1),
            )
            return {"clicked": True, "method": "trusted_pointer", **target}
        expression = (
            CLICK_RELATION_JS
            .replace("__FRAGMENTS__", json.dumps(count_href_fragments(platform, source_url, relation)))
            .replace("__LABELS__", json.dumps(RELATION_LABELS[relation]))
            .replace("__CONTROL_SELECTORS__", json.dumps(spec.control_selectors))
        )
        return tab.evaluate(expression) or {"clicked": False}

    @staticmethod
    def _advance_page(
        tab: CDPTab,
        next_expression: str,
        visited_page_urls: set[str],
        settle_seconds: float,
    ) -> bool:
        """Advance a rendered pagination control once, rejecting loops."""
        action = tab.evaluate(next_expression) or {"clicked": False}
        next_url = str(action.get("href") or "")
        if action.get("navigate") and next_url and next_url not in visited_page_urls:
            tab.clear_network_capture()
            tab.navigate(next_url, settle_seconds)
            visited_page_urls.add(tab.current_url())
            return True
        if action.get("clicked"):
            time.sleep(settle_seconds)
            tab.evaluate("delete window.__contactAnalyzerRoot")
            visited_page_urls.add(tab.current_url())
            return True
        return False

    @staticmethod
    def _network_candidates(platform: str, data: Any, source_url: str, relation: str | None = None) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        seen_obj: set[int] = set()

        def add(username: Any, display_name: Any = None, platform_id: Any = None, avatar: Any = None, explicit_url: Any = None) -> None:
            username_s = str(username or "").strip().lstrip("@")
            if not username_s:
                return
            canonical = str(explicit_url or "").strip()
            if canonical:
                normalized = normalize_profile_link(platform, canonical, source_url)
                if normalized:
                    username_n, canonical, id_n = normalized
                    username_s = username_n
                    platform_id = platform_id or id_n
                else:
                    canonical = ""
            if not canonical:
                canonical = canonical_from_network(platform, username_s, source_url, str(platform_id) if platform_id else None) or ""
            if not canonical:
                return
            normalized = normalize_profile_link(platform, canonical, source_url)
            if not normalized:
                return
            username_s, canonical, normalized_id = normalized
            platform_id = platform_id or normalized_id
            records.append({
                "platform": platform,
                "username": username_s,
                "display_name": str(display_name).strip() if display_name else None,
                "platform_user_id": str(platform_id) if platform_id else None,
                "profile_url": canonical,
                "avatar_url": str(avatar).strip() if avatar else None,
                "extraction_source": "browser_network_response",
            })

        def add_x_user(value: Any) -> None:
            if not isinstance(value, dict):
                return
            while isinstance(value.get("result"), dict):
                value = value["result"]
            legacy = value.get("legacy") if isinstance(value.get("legacy"), dict) else {}
            core = value.get("core") if isinstance(value.get("core"), dict) else {}
            avatar = value.get("avatar") if isinstance(value.get("avatar"), dict) else {}
            screen_name = legacy.get("screen_name") or core.get("screen_name") or value.get("screen_name")
            if screen_name:
                add(
                    screen_name,
                    core.get("name") or legacy.get("name") or value.get("name"),
                    value.get("rest_id") or value.get("id_str") or value.get("id"),
                    avatar.get("image_url") or legacy.get("profile_image_url_https") or value.get("profile_image_url_https"),
                )

        def walk_relationship_payload(value: Any) -> None:
            """Read only collections structurally owned by a relationship response."""
            if isinstance(value, dict):
                if platform == "x":
                    user_results = value.get("user_results")
                    if isinstance(user_results, dict):
                        add_x_user(user_results.get("result"))
                elif platform == "instagram":
                    users = value.get("users")
                    if isinstance(users, list):
                        for user in users:
                            if isinstance(user, dict) and user.get("username"):
                                add(
                                    user.get("username"),
                                    user.get("full_name") or user.get("name"),
                                    user.get("pk") or user.get("id"),
                                    user.get("profile_pic_url") or user.get("profile_pic_url_hd"),
                                )
                elif platform == "threads" and relation in {"followers", "following"}:
                    relationship = value.get(relation)
                    edges = relationship.get("edges") if isinstance(relationship, dict) else None
                    if isinstance(edges, list):
                        for edge in edges:
                            node = edge.get("node") if isinstance(edge, dict) else None
                            if isinstance(node, dict) and node.get("username"):
                                add(
                                    node.get("username"),
                                    node.get("full_name") or node.get("name"),
                                    node.get("pk") or node.get("id"),
                                    node.get("profile_pic_url"),
                                )
                elif platform == "bluesky":
                    key = "followers" if relation == "followers" else "follows"
                    actors = value.get(key)
                    if isinstance(actors, list):
                        for actor in actors:
                            if isinstance(actor, dict) and actor.get("handle"):
                                add(actor.get("handle"), actor.get("displayName"), actor.get("did"), actor.get("avatar"))
                for child in value.values():
                    walk_relationship_payload(child)
            elif isinstance(value, list):
                for child in value:
                    walk_relationship_payload(child)

        def walk_generic(value: Any) -> None:
            if isinstance(value, dict):
                identity = id(value)
                if identity in seen_obj:
                    return
                seen_obj.add(identity)
                if platform in {"instagram", "threads"} and value.get("username"):
                    add(value.get("username"), value.get("full_name") or value.get("name"), value.get("pk") or value.get("id"), value.get("profile_pic_url") or value.get("profile_pic_url_hd"))
                elif platform == "tiktok":
                    unique = value.get("uniqueId") or value.get("unique_id")
                    if unique:
                        avatar = value.get("avatarThumb") or value.get("avatar_thumb")
                        if isinstance(avatar, dict):
                            avatar = (avatar.get("url_list") or [None])[0]
                        add(unique, value.get("nickname"), value.get("id") or value.get("uid"), avatar)
                elif platform == "github" and value.get("login"):
                    add(value.get("login"), value.get("name"), value.get("id"), value.get("avatar_url"), value.get("html_url"))
                elif platform == "mastodon" and (value.get("acct") or value.get("username")):
                    add(value.get("acct") or value.get("username"), value.get("display_name"), value.get("id"), value.get("avatar"), value.get("url"))
                elif platform == "facebook" and value.get("name") and value.get("id"):
                    url = value.get("url") or value.get("profile_url")
                    username = value.get("username") or value.get("id")
                    add(username, value.get("name"), value.get("id"), value.get("profile_picture") or value.get("picture"), url)
                elif platform == "poshmark" and value.get("username"):
                    add(
                        value.get("username"),
                        value.get("full_name") or value.get("display_handle"),
                        value.get("id"),
                        value.get("picture_url"),
                    )
                for child in value.values():
                    walk_generic(child)
            elif isinstance(value, list):
                for child in value:
                    walk_generic(child)

        if platform in {"instagram", "threads", "x", "bluesky"}:
            walk_relationship_payload(data)
        else:
            walk_generic(data)
        return records

    @staticmethod
    def _merge_record(records_by_url: dict[str, dict[str, Any]], record: dict[str, Any]) -> bool:
        """Validate and merge a candidate using its canonical profile URL as identity."""
        platform = str(record.get("platform") or "")
        source_url = str(record.get("source_profile_url") or "")
        normalized = normalize_profile_link(platform, str(record.get("profile_url") or ""), source_url)
        if not normalized:
            return False
        username, canonical, normalized_id = normalized
        source_username, _ = source_identity(platform, source_url)
        if username.casefold() == source_username.casefold():
            return False

        candidate = dict(record)
        candidate["username"] = username
        candidate["profile_url"] = canonical
        candidate["platform_user_id"] = candidate.get("platform_user_id") or normalized_id
        key = canonical.casefold()
        existing = records_by_url.get(key)
        if not existing:
            records_by_url[key] = candidate
            return True

        for field in ("display_name", "avatar_url", "platform_user_id"):
            if not existing.get(field) and candidate.get(field):
                existing[field] = candidate[field]
        sources: set[str] = set()
        for value in (existing.get("extraction_source"), candidate.get("extraction_source")):
            sources.update(source.strip() for source in str(value or "").split("+") if source.strip())
        if sources:
            existing["extraction_source"] = "+".join(sorted(sources))
        return False

    @staticmethod
    def _dom_records(platform: str, source_url: str, relation: str, state: dict[str, Any]) -> list[dict[str, Any]]:
        source_username, _ = source_identity(platform, source_url)
        output: list[dict[str, Any]] = []
        for raw in state.get("records", []):
            normalized = normalize_profile_link(platform, str(raw.get("href") or ""), source_url)
            if not normalized:
                continue
            username, canonical, platform_id = normalized
            if username.casefold() == source_username.casefold():
                continue
            anchor_text = str(raw.get("anchorText") or "").strip()
            item_text = str(raw.get("itemText") or "").strip()
            image_alt = str(raw.get("imageAlt") or "").strip()
            display = anchor_text or image_alt or None
            if display and display.casefold().lstrip("@") == username.casefold():
                pieces = [x.strip() for x in re.split(r"[\n\r|]+", item_text) if x.strip()]
                for piece in pieces:
                    if piece.casefold().lstrip("@") != username.casefold() and len(piece) <= 120:
                        display = piece
                        break
            if platform == "threads":
                display = threads_display_name(username, display or item_text)
            output.append({
                "platform": platform,
                "relationship": relation,
                "source_profile_url": source_url,
                "platform_user_id": platform_id,
                "username": username,
                "display_name": display,
                "profile_url": canonical,
                "avatar_url": raw.get("imageSrc") or None,
                "collected_at": utc_now(),
                "extraction_source": "visible_browser_dom",
            })
        return output

    def _collect_facebook_friend_filters(
        self,
        tab: CDPTab,
        source_url: str,
        records_by_key: dict[str, dict[str, Any]],
        selector_json: str,
        reported_count: int,
        settle_seconds: float,
    ) -> dict[str, Any]:
        """Union source-scoped Facebook friend-subset tabs after an incomplete pass."""
        def validated_filters(raw_tabs: list[dict[str, Any]]) -> list[dict[str, str]]:
            output: list[dict[str, str]] = []
            seen_routes: set[str] = set()
            for raw in raw_tabs:
                route = facebook_friend_filter_route(source_url, str(raw.get("href") or ""))
                if not route or route.casefold() in seen_routes:
                    continue
                seen_routes.add(route.casefold())
                output.append({"text": str(raw.get("text") or "Friend filter"), "url": route})
            return output

        raw_tabs = tab.evaluate(FACEBOOK_FRIEND_FILTER_TABS_JS, timeout=10) or []
        filters = validated_filters(raw_tabs)
        reopened_all_friends = False
        if not filters:
            # A long Facebook directory can virtualize the tab bar out of the
            # live DOM. Reopen the verified All friends route before concluding
            # that no supplemental filters exist.
            all_friends_route = relation_url("facebook", source_url, "friends")
            if all_friends_route:
                tab.navigate(all_friends_route, settle_seconds, timeout=20)
                tab.evaluate("delete window.__contactAnalyzerRoot", timeout=10)
                reopened_all_friends = True
                filters = validated_filters(
                    tab.evaluate(FACEBOOK_FRIEND_FILTER_TABS_JS, timeout=10) or []
                )

        diagnostics: dict[str, Any] = {
            "discovered": filters,
            "attempts": [],
            "added_unique": 0,
            "reopened_all_friends": reopened_all_friends,
        }
        if not filters:
            return diagnostics

        delay = float(self.settings.get("scroll_delay_seconds", 1.35))
        stall_limit = int(self.settings.get("stall_round_limit", 22))
        end_stall_limit = int(self.settings.get("end_stall_round_limit", 3))
        content_stall_limit = max(
            1,
            int(self.settings.get("facebook_content_stall_round_limit", 12)),
        )
        loading_stall_limit = max(
            stall_limit,
            int(self.settings.get("facebook_loading_stall_round_limit", 35)),
        )
        loading_content_stall_limit = max(
            content_stall_limit,
            int(self.settings.get("facebook_loading_content_stall_round_limit", 20)),
        )
        max_rounds = max(
            loading_stall_limit,
            int(self.settings.get("facebook_friend_filter_max_rounds", 80)),
        )
        total_before = len(records_by_key)

        for friend_filter in filters:
            if len(records_by_key) >= reported_count:
                break
            route = friend_filter["url"]
            label = friend_filter["text"]
            route_before = len(records_by_key)
            tab.navigate(route, settle_seconds, timeout=20)
            tab.evaluate("delete window.__contactAnalyzerRoot", timeout=10)
            stall_rounds = 0
            content_stall_rounds = 0
            end_stall_rounds = 0
            last_position: tuple[int, int] | None = None
            stop_reason = "maximum_filter_rounds_reached"
            round_no = 0
            blocked_text: str | None = None
            for round_no in range(1, max_rounds + 1):
                state = tab.evaluate(
                    LIST_STATE_JS
                    .replace("__SELECTORS__", selector_json)
                    .replace("__PLATFORM__", json.dumps("facebook"))
                    .replace("__RELATION__", json.dumps("friends")),
                    timeout=10,
                ) or {}
                blocked_text = state.get("blockedText")
                if blocked_text:
                    stop_reason = f"browser_blocked:{blocked_text}"
                    break

                before = len(records_by_key)
                for record in self._dom_records("facebook", source_url, "friends", state):
                    record["extraction_source"] = "visible_browser_facebook_friend_filter"
                    self._merge_record(records_by_key, record)
                added = len(records_by_key) - before
                position = (
                    int(state.get("scrollTop") or 0),
                    int(state.get("scrollHeight") or 0),
                )
                if added > 0 or position != last_position:
                    stall_rounds = 0
                else:
                    stall_rounds += 1
                content_stall_rounds = 0 if added > 0 else content_stall_rounds + 1
                if state.get("atEnd") and added == 0 and not state.get("spinner"):
                    end_stall_rounds += 1
                elif added > 0 or not state.get("atEnd"):
                    end_stall_rounds = 0
                last_position = position

                if len(records_by_key) >= reported_count:
                    stop_reason = "reported_count_reached"
                    break
                active_stall_limit = loading_stall_limit if state.get("spinner") else stall_limit
                content_stable = bool(
                    content_stall_rounds >= content_stall_limit
                    and not state.get("spinner")
                )
                loading_without_new_rows = bool(
                    state.get("spinner")
                    and content_stall_rounds >= loading_content_stall_limit
                )
                if (
                    end_stall_rounds >= end_stall_limit
                    or stall_rounds >= active_stall_limit
                    or content_stable
                    or loading_without_new_rows
                ):
                    stop_reason = (
                        "facebook_loading_without_new_rows"
                        if loading_without_new_rows
                        else "friend_filter_conclusively_exhausted"
                    )
                    break
                tab.evaluate(
                    SCROLL_JS.replace("__SELECTORS__", selector_json),
                    timeout=10,
                )
                time.sleep(delay * 1.8 if state.get("spinner") else delay)

            route_added = len(records_by_key) - route_before
            diagnostics["attempts"].append({
                "text": label,
                "url": route,
                "rounds": round_no,
                "added_unique": route_added,
                "deduplicated_total": len(records_by_key),
                "stop_reason": stop_reason,
                "blocked_text": blocked_text,
            })
            print(
                f"  [facebook:friends] supplemental filter {label}: "
                f"+{route_added} unique; {len(records_by_key)}/{reported_count} total"
            )

        diagnostics["added_unique"] = len(records_by_key) - total_before
        diagnostics["deduplicated_total"] = len(records_by_key)
        return diagnostics

    def collect(
        self,
        *,
        platform: str,
        source_url: str,
        relation: str,
        diagnostics_dir: Path,
        checkpoint_path: Path,
    ) -> CollectionOutcome:
        if platform == "tiktok":
            return self._collect_tiktok(source_url, relation, diagnostics_dir, checkpoint_path)
        if platform == "threads":
            return self._collect_threads(source_url, relation, diagnostics_dir, checkpoint_path)
        started = utc_now()
        spec = SPECS.get(platform, SPECS["generic"])
        tab = self.browser.new_tab("about:blank")
        reported_count: int | None = None
        reported_raw: str | None = None
        count_candidates: list[dict[str, Any]] = []
        open_action: dict[str, Any] = {}
        retry_actions: list[dict[str, Any]] = []
        records_by_key: dict[str, dict[str, Any]] = {}
        pages_visited = 1
        list_pass = 1
        completion_retries = 0
        collection_policy = relationship_collection_policy(platform, self.settings)
        completion_retry_limit = int(collection_policy["completion_retry_limit"] or 0)
        content_stall_limit = collection_policy["content_stall_round_limit"]
        stall_rounds = 0
        content_stall_rounds = 0
        end_stall_rounds = 0
        last_position: tuple[int, int] | None = None
        visited_page_urls: set[str] = set()
        reason = "unknown"
        blocked_text: str | None = None
        relationship_payloads: list[dict[str, Any]] = []
        relationship_payload_exhausted = False
        relationship_payload_has_more: bool | None = None
        facebook_friend_filters: dict[str, Any] | None = None
        effective_source_url = source_url
        settle_seconds = float(self.settings.get("settle_seconds", 3.0))
        if platform == "facebook":
            settle_seconds = max(settle_seconds, 12.0)
        try:
            tab.navigate(source_url, settle_seconds)
            if platform == "x":
                unavailable_state = tab.evaluate(X_PROFILE_UNAVAILABLE_STATE_JS) or {}
                if unavailable_state.get("unavailable"):
                    reason = "source_profile_unavailable"
                    diagnostics = {
                        "source_profile_unavailable": True,
                        "evidence": unavailable_state.get("evidence"),
                        "current_url": tab.current_url(),
                        "title": unavailable_state.get("title") or tab.title(),
                    }
                    diagnostics_dir.mkdir(parents=True, exist_ok=True)
                    tab.screenshot(diagnostics_dir / f"{relation}-unavailable.png")
                    tab.save_html(diagnostics_dir / f"{relation}-unavailable.html")
                    write_json(diagnostics_dir / f"{relation}-unavailable.json", diagnostics)
                    return CollectionOutcome(
                        platform, source_url, relation, None, None, 0,
                        "blocked", reason, started, utc_now(), [], diagnostics,
                    )
            if platform == "facebook":
                # Facebook canonicalizes Unicode vanity names in-place (for
                # example dotted-I to ASCII i). Count/link matching and source
                # rejection must use that live canonical profile URL.
                current_source = tab.current_url().split("#", 1)[0].rstrip("/")
                if (
                    "facebook.com/" in current_source.casefold()
                    and not any(
                        marker in current_source.casefold()
                        for marker in ("/login", "/checkpoint", "/recover")
                    )
                ):
                    effective_source_url = current_source
            if platform == "instagram":
                private_state = tab.evaluate(INSTAGRAM_PRIVATE_STATE_JS) or {}
                if private_state.get("private"):
                    reported_raw = str((private_state.get("counts") or {}).get(relation) or "") or None
                    reported_count = parse_count(reported_raw) if reported_raw else None
                    reason = "private_profile_relationship_list_unavailable"
                    diagnostics = {
                        "private_profile": True,
                        "evidence": private_state.get("evidence"),
                        "current_url": tab.current_url(),
                        "displayed_counts": private_state.get("counts") or {},
                    }
                    diagnostics_dir.mkdir(parents=True, exist_ok=True)
                    tab.screenshot(diagnostics_dir / f"{relation}-private.png")
                    tab.save_html(diagnostics_dir / f"{relation}-private.html")
                    write_json(diagnostics_dir / f"{relation}-private.json", diagnostics)
                    return CollectionOutcome(
                        platform, source_url, relation, reported_count, reported_raw, 0,
                        "private", reason, started, utc_now(), [], diagnostics,
                    )
            reported_count, reported_raw, count_candidates, count_hydration_retries = (
                self._count_with_hydration_retry(
                    tab,
                    platform,
                    effective_source_url,
                    relation,
                    settle_seconds,
                )
            )
            print(f"  [{platform}:{relation}] displayed count: {reported_count if reported_count is not None else 'unknown'}")

            if platform in {"pinterest", "depop", "poshmark", "disqus", "soundcloud", "quora"} and reported_count == 0:
                reason = "collected_unique_equals_reported_count"
                diagnostics = {
                    "count_candidates": count_candidates,
                    "open_action": {"method": "exact_zero_count", "clicked": False},
                    "current_url": tab.current_url(),
                    "count_hydration_retries": count_hydration_retries,
                }
                write_json(checkpoint_path, {
                    "platform": platform,
                    "source_profile_url": source_url,
                    "relation": relation,
                    "reported_count": 0,
                    "collected_this_run": 0,
                    "updated_at": utc_now(),
                    "records": [],
                })
                return CollectionOutcome(
                    platform, source_url, relation, 0, reported_raw, 0,
                    "complete", reason, started, utc_now(), [], diagnostics,
                )

            direct = relation_url(platform, effective_source_url, relation)
            if direct:
                open_action = {"method": "direct_route", "url": direct}
                tab.clear_network_capture()
                tab.navigate(direct, settle_seconds)
            else:
                tab.clear_network_capture()
                open_action = self._click_relation(
                    tab, platform, effective_source_url, relation
                )
                if open_action.get("clicked"):
                    time.sleep(settle_seconds)
                elif self.settings.get("manual_rescue", True):
                    print(f"  Open the {relation} list in the visible Chromium tab, then press Enter here.")
                    input()
                    open_action = {"method": "manual_rescue", "clicked": True}
                else:
                    reason = "relation_control_not_found"
                    return CollectionOutcome(
                        platform, source_url, relation, reported_count, reported_raw, 0,
                        "failed", reason, started, utc_now(), [],
                        {
                            "count_candidates": count_candidates,
                            "open_action": open_action,
                            "count_hydration_retries": count_hydration_retries,
                        },
                    )

            if platform == "github":
                empty_state = tab.evaluate(
                    GITHUB_EMPTY_STATE_JS.replace("__RELATION__", json.dumps(relation))
                ) or {}
                if empty_state.get("empty"):
                    diagnostics = {
                        "count_candidates": count_candidates,
                        "open_action": open_action,
                        "current_url": tab.current_url(),
                        "explicit_empty_state": True,
                    }
                    write_json(checkpoint_path, {
                        "platform": platform,
                        "source_profile_url": source_url,
                        "relation": relation,
                        "reported_count": 0,
                        "collected_this_run": 0,
                        "updated_at": utc_now(),
                        "records": [],
                    })
                    return CollectionOutcome(
                        platform, source_url, relation, 0, "0", 0,
                        "complete", "collected_unique_equals_reported_count",
                        started, utc_now(), [], diagnostics,
                    )

            if platform == "facebook":
                unavailable_state = tab.evaluate(
                    FACEBOOK_RELATION_UNAVAILABLE_STATE_JS.replace(
                        "__RELATION__", json.dumps(relation)
                    )
                ) or {}
                if unavailable_state.get("unavailable"):
                    if reported_count == 0:
                        diagnostics = {
                            "count_candidates": count_candidates,
                            "open_action": open_action,
                            "explicit_empty_state": True,
                            "evidence": unavailable_state.get("evidence"),
                            "current_url": tab.current_url(),
                        }
                        return CollectionOutcome(
                            platform, source_url, relation, 0, reported_raw, 0,
                            "complete", "collected_unique_equals_reported_count",
                            started, utc_now(), [], diagnostics,
                        )
                    reason = "private_profile_relationship_list_unavailable"
                    diagnostics = {
                        "count_candidates": count_candidates,
                        "open_action": open_action,
                        "private_relationship_list": True,
                        "evidence": unavailable_state.get("evidence"),
                        "current_url": tab.current_url(),
                    }
                    diagnostics_dir.mkdir(parents=True, exist_ok=True)
                    tab.screenshot(diagnostics_dir / f"{relation}-private.png")
                    tab.save_html(diagnostics_dir / f"{relation}-private.html")
                    write_json(diagnostics_dir / f"{relation}-private.json", diagnostics)
                    return CollectionOutcome(
                        platform, source_url, relation, reported_count, reported_raw, 0,
                        "private", reason, started, utc_now(), [], diagnostics,
                    )

            selector_json = json.dumps(spec.row_selectors)
            next_json = json.dumps(spec.next_selectors)
            max_rounds = int(self.settings.get("max_scroll_rounds", 100000))
            stall_limit = int(self.settings.get("stall_round_limit", 22))
            end_stall_limit = int(self.settings.get("end_stall_round_limit", 3))
            max_pages = int(self.settings.get("max_pagination_pages", 10000))
            delay = float(self.settings.get("scroll_delay_seconds", 1.35))
            keywords = network_keywords(platform, relation)
            visited_page_urls.add(tab.current_url())
            relation_started_monotonic = time.monotonic()

            for round_no in range(1, max_rounds + 1):
                state = tab.evaluate(
                    LIST_STATE_JS
                    .replace("__SELECTORS__", selector_json)
                    .replace("__PLATFORM__", json.dumps(platform))
                    .replace("__RELATION__", json.dumps(relation))
                ) or {}
                blocked_text = state.get("blockedText")
                if blocked_text:
                    reason = f"browser_blocked:{blocked_text}"
                    break

                before = len(records_by_key)
                dom_records = self._dom_records(
                    platform, effective_source_url, relation, state
                )
                visible_usernames = [str(record.get("username") or "-") for record in dom_records]
                for record in dom_records:
                    self._merge_record(records_by_key, record)

                if self.settings.get("network_capture", True) and keywords:
                    for response_url, payload in tab.drain_json_responses(keywords):
                        for page in relationship_payload_pages(platform, response_url, payload, relation):
                            page_record = {
                                **page,
                                "url": response_url,
                                "list_pass": list_pass,
                            }
                            relationship_payloads.append(page_record)
                            if page.get("has_more") is False:
                                relationship_payload_exhausted = True
                                relationship_payload_has_more = False
                            elif page.get("has_more") is True:
                                relationship_payload_has_more = True
                        for record in self._network_candidates(
                            platform, payload, effective_source_url, relation
                        ):
                            record.update({
                                "relationship": relation,
                                "source_profile_url": source_url,
                                "collected_at": utc_now(),
                            })
                            self._merge_record(records_by_key, record)

                added = len(records_by_key) - before
                position = (int(state.get("scrollTop") or 0), int(state.get("scrollHeight") or 0))
                if added > 0 or position != last_position:
                    stall_rounds = 0
                else:
                    stall_rounds += 1
                if added > 0:
                    content_stall_rounds = 0
                else:
                    content_stall_rounds += 1
                if state.get("atEnd") and added == 0 and not state.get("spinner"):
                    end_stall_rounds += 1
                elif added > 0 or not state.get("atEnd"):
                    end_stall_rounds = 0
                last_position = position

                expected = str(reported_count) if reported_count is not None else "?"
                print(
                    f"\r  [{platform}:{relation}] {len(records_by_key)}/{expected} "
                    f"round={round_no} added={added} stalls={stall_rounds}/{content_stall_rounds} "
                    f"pages={pages_visited} pass={list_pass} "
                    f"visible={len(visible_usernames)} "
                    f"first={visible_usernames[0] if visible_usernames else '-'} "
                    f"last={visible_usernames[-1] if visible_usernames else '-'} "
                    f"scroll={position[0]}/{position[1]} "
                    f"viewport={int(state.get('clientHeight') or 0)}      ",
                    end="",
                    flush=True,
                )

                write_json(checkpoint_path, {
                    "platform": platform,
                    "source_profile_url": source_url,
                    "relation": relation,
                    "reported_count": reported_count,
                    "collected_this_run": len(records_by_key),
                    "round": round_no,
                    "pages_visited": pages_visited,
                    "list_pass": list_pass,
                    "relationship_payload_exhausted": relationship_payload_exhausted,
                    "relationship_payload_has_more": relationship_payload_has_more,
                    "content_stall_rounds": content_stall_rounds,
                    "updated_at": utc_now(),
                    "records": list(records_by_key.values()),
                })

                if reported_count is not None and len(records_by_key) >= reported_count:
                    reason = "reported_count_reached"
                    break

                browser_limited_content_stable = bool(
                    platform in {"facebook", "instagram", "x"}
                    and records_by_key
                    and content_stall_limit is not None
                    and content_stall_rounds >= content_stall_limit
                    # Instagram can leave a loading indicator mounted while its
                    # virtualized modal keeps replaying the same canonical rows.
                    # Canonical-row stability is the useful progress signal there.
                    and (not state.get("spinner") or platform == "instagram")
                )
                facebook_loading_without_new_rows = bool(
                    platform == "facebook"
                    and state.get("spinner")
                    and records_by_key
                    and content_stall_rounds >= max(
                        int(content_stall_limit or 1),
                        int(self.settings.get(
                            "facebook_loading_content_stall_round_limit", 20
                        )),
                    )
                )
                facebook_zero_row_limit_reached = bool(
                    platform == "facebook"
                    and not records_by_key
                    and round_no >= max(
                        1,
                        int(self.settings.get("facebook_zero_row_max_rounds", 45)),
                    )
                )
                facebook_relation_time_limit_reached = bool(
                    platform == "facebook"
                    and time.monotonic() - relation_started_monotonic >= max(
                        1.0,
                        float(self.settings.get("facebook_relation_max_seconds", 90)),
                    )
                )
                active_stall_limit = stall_limit
                if platform == "facebook" and state.get("spinner"):
                    active_stall_limit = max(
                        stall_limit,
                        int(self.settings.get("facebook_loading_stall_round_limit", 35)),
                    )
                if (
                    end_stall_rounds >= end_stall_limit
                    or stall_rounds >= active_stall_limit
                    or browser_limited_content_stable
                    or facebook_loading_without_new_rows
                    or facebook_zero_row_limit_reached
                    or facebook_relation_time_limit_reached
                ):
                    if facebook_relation_time_limit_reached:
                        reason = "facebook_browser_relation_time_limit_reached"
                        break
                    # Facebook and X relationship views are virtualized,
                    # infinite-scroll directories. Their page can contain
                    # unrelated navigation links that resemble pagination.
                    if platform not in {"facebook", "x"} and pages_visited < max_pages:
                        advanced = self._advance_page(
                            tab,
                            NEXT_PAGE_JS.replace("__SELECTORS__", next_json),
                            visited_page_urls,
                            settle_seconds,
                        )
                        if advanced:
                            pages_visited += 1
                            stall_rounds = 0
                            content_stall_rounds = 0
                            end_stall_rounds = 0
                            last_position = None
                            continue
                    if (
                        reported_count is not None
                        and len(records_by_key) < reported_count
                        and completion_retries < completion_retry_limit
                    ):
                        completion_retries += 1
                        list_pass += 1
                        print(
                            f"\n  [{platform}:{relation}] exact-count retry "
                            f"{completion_retries}/{completion_retry_limit}: "
                            f"{len(records_by_key)}/{reported_count} collected; reopening the verified list"
                        )
                        tab.navigate(effective_source_url, settle_seconds)
                        retry_count, retry_raw, retry_candidates = self._count(
                            tab, platform, effective_source_url, relation
                        )
                        if retry_count is not None:
                            reported_count, reported_raw = retry_count, retry_raw
                            count_candidates.extend(retry_candidates)
                        tab.clear_network_capture()
                        if direct:
                            retry_action = {"method": "direct_route", "url": direct, "list_pass": list_pass}
                            tab.navigate(direct, settle_seconds)
                        else:
                            retry_action = self._click_relation(
                                tab, platform, effective_source_url, relation
                            )
                            retry_action["list_pass"] = list_pass
                            if retry_action.get("clicked"):
                                time.sleep(settle_seconds)
                        retry_actions.append(retry_action)
                        if direct or retry_action.get("clicked"):
                            tab.evaluate("delete window.__contactAnalyzerRoot")
                            stall_rounds = 0
                            content_stall_rounds = 0
                            end_stall_rounds = 0
                            last_position = None
                            relationship_payload_exhausted = False
                            relationship_payload_has_more = None
                            continue
                        reason = "exact_count_retry_relation_control_not_found"
                        break
                    reason = "rendered_list_conclusively_exhausted"
                    break

                if platform == "x":
                    target = tab.evaluate(
                        X_SCROLL_TARGET_JS.replace("__RELATION__", json.dumps(relation))
                    ) or {}
                    self._x_trusted_scroll(
                        tab,
                        float(target.get("x") or 1),
                        float(target.get("y") or 1),
                        int(state.get("clientHeight") or 1),
                    )
                else:
                    tab.evaluate(SCROLL_JS.replace("__SELECTORS__", selector_json))
                if state.get("spinner"):
                    time.sleep(delay * 1.8)
                else:
                    time.sleep(delay + random.uniform(0.05, 0.30))
            else:
                reason = "maximum_scroll_rounds_reached"

            print()

            if (
                platform == "facebook"
                and relation == "friends"
                and reported_count is not None
                and 0 < len(records_by_key) < reported_count
                and not blocked_text
            ):
                try:
                    facebook_friend_filters = self._collect_facebook_friend_filters(
                        tab,
                        effective_source_url,
                        records_by_key,
                        selector_json,
                        reported_count,
                        settle_seconds,
                    )
                except Exception as exc:
                    # Supplemental filters improve coverage but must never
                    # invalidate or strand the canonical rows from the main
                    # source-scoped friends directory.
                    facebook_friend_filters = {
                        "discovered": [],
                        "attempts": [],
                        "added_unique": 0,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                write_json(checkpoint_path, {
                    "platform": platform,
                    "source_profile_url": source_url,
                    "relation": relation,
                    "reported_count": reported_count,
                    "collected_this_run": len(records_by_key),
                    "round": round_no,
                    "pages_visited": pages_visited,
                    "list_pass": list_pass,
                    "facebook_friend_filters": facebook_friend_filters,
                    "updated_at": utc_now(),
                    "records": list(records_by_key.values()),
                })

            records = sorted(
                records_by_key.values(),
                key=lambda x: (str(x.get("username") or "").casefold(), x["profile_url"]),
            )
            collected = len(records)
            if blocked_text:
                status = "blocked"
            elif reported_count is not None and collected == reported_count:
                status = "complete"
                reason = "collected_unique_equals_reported_count"
            elif trusted_exhausted_instagram_count_lag(
                platform,
                reported_count,
                records,
                relationship_payload_exhausted,
                relationship_payload_has_more,
            ):
                # Save the fully corroborated rows, but cumulative reporting
                # remains Review because saved coverage exceeds the stale count.
                status = "incomplete"
                reason = "trusted_exhausted_list_exceeds_stale_displayed_count_by_one"
            elif reported_count is not None and collected > reported_count:
                status = "review"
                reason = "collected_more_than_reported_count"
            elif reported_count is None and reason == "rendered_list_conclusively_exhausted" and collected > 0:
                status = "complete_accessible_list"
            elif (
                reported_count is not None
                and collected < reported_count
                and reason in {
                    "rendered_list_conclusively_exhausted",
                    "maximum_scroll_rounds_reached",
                }
            ):
                status = "incomplete"
                reason = (
                    "platform_relationship_payload_exhausted_before_displayed_count"
                    if relationship_payload_exhausted
                    else (
                        "browser_relationship_cursor_not_requested"
                        if relationship_payload_has_more
                        else "displayed_count_exceeds_accessible_list"
                    )
                )
            elif collected > 0:
                status = "incomplete"
                if reported_count is not None and reason == "rendered_list_conclusively_exhausted":
                    reason = (
                        "platform_relationship_payload_exhausted_before_displayed_count"
                        if relationship_payload_exhausted
                        else (
                            "browser_relationship_cursor_not_requested"
                            if relationship_payload_has_more
                            else "displayed_count_exceeds_accessible_list"
                        )
                    )
            else:
                status = "failed"

            diagnostics = {
                "count_candidates": count_candidates,
                "open_action": open_action,
                "retry_actions": retry_actions,
                "effective_source_url": effective_source_url,
                "current_url": tab.current_url(),
                "title": tab.title(),
                "pages_visited": pages_visited,
                "last_position": last_position,
                "network_keywords": list(keywords),
                "list_passes": list_pass,
                "completion_retries": completion_retries,
                "collection_policy": collection_policy,
                "content_stall_rounds": content_stall_rounds,
                "relationship_payloads": relationship_payloads,
                "relationship_payload_exhausted": relationship_payload_exhausted,
                "relationship_payload_has_more": relationship_payload_has_more,
                "facebook_friend_filters": facebook_friend_filters,
                "displayed_count_gap": (
                    max(0, reported_count - collected) if reported_count is not None else None
                ),
            }
            if status not in {"complete", "complete_accessible_list"}:
                diagnostics_dir.mkdir(parents=True, exist_ok=True)
                tab.screenshot(diagnostics_dir / f"{relation}.png")
                tab.save_html(diagnostics_dir / f"{relation}.html")
                write_json(diagnostics_dir / f"{relation}.json", diagnostics)

            return CollectionOutcome(
                platform=platform,
                source_profile_url=source_url,
                relation=relation,
                reported_count=reported_count,
                reported_count_raw=reported_raw,
                collected_this_run=collected,
                status=status,
                reason=reason,
                started_at=started,
                completed_at=utc_now(),
                records=records,
                diagnostics=diagnostics,
            )
        except Exception as exc:
            records = sorted(
                records_by_key.values(),
                key=lambda item: (
                    str(item.get("username") or "").casefold(),
                    str(item.get("profile_url") or ""),
                ),
            )
            collected = len(records)
            error = f"{type(exc).__name__}: {exc}"
            # A late CDP/control timeout must not invalidate canonical rows that
            # were already scoped, normalized, and checkpointed. Preserve that
            # trusted partial pass while keeping it explicitly incomplete.
            status = "incomplete" if collected else "failed"
            outcome_reason = (
                "browser_control_error_after_partial_collection"
                if collected
                else error
            )
            try:
                diagnostics_dir.mkdir(parents=True, exist_ok=True)
                tab.screenshot(diagnostics_dir / f"{relation}.png")
                tab.save_html(diagnostics_dir / f"{relation}.html")
            except Exception:
                pass
            return CollectionOutcome(
                platform=platform,
                source_profile_url=source_url,
                relation=relation,
                reported_count=reported_count,
                reported_count_raw=reported_raw,
                collected_this_run=collected,
                status=status,
                reason=outcome_reason,
                started_at=started,
                completed_at=utc_now(),
                records=records,
                diagnostics={
                    "count_candidates": count_candidates,
                    "open_action": open_action,
                    "collection_error": error,
                    "displayed_count_gap": (
                        max(0, reported_count - collected)
                        if reported_count is not None
                        else None
                    ),
                },
            )
        finally:
            tab.close(close_target=True)
