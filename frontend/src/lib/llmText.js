/**
 * Strip chain-of-thought, commands, and prompt junk from LLM narrative text.
 * Mirrors backend strip_reasoning_process for display of persisted insights.
 */

const THINKING_BLOCK_RE = /<\s*think(?:ing)?\s*>[\s\S]*?<\s*\/\s*think(?:ing)?\s*>/gi;
const REASONING_TAG_RE = /<\s*reasoning\s*>[\s\S]*?<\s*\/\s*reasoning\s*>/gi;
const THINK_CLOSE_SPLIT_RE = /<\s*\/\s*think(?:ing)?\s*>/i;
const CODE_FENCE_RE = /```[\w-]*\s*[\s\S]*?```/g;
const FINAL_LABEL_RE = /(?:^|\n)\s*(?:final(?:\s+answer)?|summary|conclusion|answer|response|explanation)\s*[:\-]\s*/i;
const PROMPT_ECHO_RE = /(?:Explain this single backtest entry fill|Summarize this chart analyst insight)[^\n.!?]{0,80}[:\s]*/gi;
const COMMAND_INLINE_RE = /\b(?:ollama\s+run|python3?\s+-m|curl\s+-[A-Za-z]|npm\s+run|pip\s+install|bash\s+-c|docker\s+run|uv\s+run)\b[^\n.!?]{0,160}/gi;
const META_QUESTION_RE = /(?:would you like|do you want|shall i|can i help|should i|let me know if)[^?.!?\n]{0,120}\?/gi;

const JUNK_LINE_RES = [
  /^\s*(?:```|~~~)/i,
  /^\s*\$\s+\S/,
  /^\s*(?:ollama|python3?|curl|npm|pip|bash|sh|cmd|powershell)\s+/i,
  /^\s*(?:run|execute|try)\s*(?:command|this)?\s*[:`]?\s*(?:ollama|python|curl)/i,
  /^\s*run\s*:\s*/i,
  /^\s*(?:explain this single backtest|summarize this chart|here(?:'s| is) the json|the user (?:wants|asked|provided|query)|based on the (?:json|prompt|provided)|looking at the json|from the json|step \d+)/i,
  /^\s*(?:would you like|do you want|shall i|can i help|let me know if|anything else|need any (?:more|further))/i,
  /^\s*(?:question|prompt|instruction)s?\s*[:\?]/i,
  /^\s*(?:analysis|reasoning|thinking)\s*(?:process|steps?)?\s*:/i,
  /^\s*#{1,6}\s*(?:analysis|reasoning|thinking|step)/i,
  /^\s*(?:thought process|internal monologue|scratchpad|my reasoning)\s*:/i,
  /^\s*\[\d+\]\s*(?:analyze|check|review|first|step)/i,
  /^\s*>\s+/,
];

const COT_OPENERS = [
  'let me',
  'i need to',
  'first,',
  'step 1',
  'thinking:',
  'the user wants',
  "i'll analyze",
  'i will analyze',
  'okay,',
  'alright,',
  'we are given',
  'we need to',
  'looking at the json',
  'from the json',
];

const META_QUESTION_MARKERS = [
  'would you',
  'do you',
  'shall i',
  'can i help',
  'should i',
  'want me to',
  'like me to',
  'need any',
  'anything else',
  'more details',
  'clarify',
  'confirm if',
];

const TRADE_NARRATIVE_HINTS = [
  'buy',
  'sell',
  'entry',
  'fill',
  'bar',
  'momentum',
  'rsi',
  'signal',
  'breakout',
  'cross',
  'long',
  'short',
  'position',
];

function looksLikeChainOfThought(text) {
  const sample = text.slice(0, 320).toLowerCase();
  return COT_OPENERS.some((marker) => sample.includes(marker));
}

function isMetaQuestionLine(line) {
  if (!line.includes('?')) return false;
  const lower = line.toLowerCase();
  return META_QUESTION_MARKERS.some((marker) => lower.includes(marker));
}

function filterJunkLines(text) {
  let lines = text
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => {
      if (!line) return false;
      if (JUNK_LINE_RES.some((re) => re.test(line))) return false;
      if (isMetaQuestionLine(line)) return false;
      return true;
    });
  if (lines.length >= 2) {
    const nonCot = lines.filter((line) => !looksLikeChainOfThought(line));
    if (nonCot.length > 0) lines = nonCot;
  }
  return lines.join('\n');
}

function isMostlyJson(text) {
  const t = text.trim();
  if (t.length < 2) return false;
  if ((t.startsWith('{') && t.endsWith('}')) || (t.startsWith('[') && t.endsWith(']'))) {
    try {
      JSON.parse(t);
      return true;
    } catch {
      return false;
    }
  }
  return false;
}

function scoreNarrativeBlock(block) {
  const lower = block.toLowerCase();
  let score = 0;
  if (looksLikeChainOfThought(block)) score -= 3;
  if (isMetaQuestionLine(block)) score -= 5;
  if (TRADE_NARRATIVE_HINTS.some((hint) => lower.includes(hint))) score += 2;
  if (block.length >= 40 && block.length <= 420) score += 1;
  if (block.length > 600) score -= 1.5;
  return score;
}

function pickNarrativeBlock(text) {
  let blocks = text.split(/\n\s*\n/).map((b) => b.trim()).filter(Boolean);
  if (blocks.length <= 1) {
    const lineBlocks = text.split('\n').map((b) => b.trim()).filter(Boolean);
    if (lineBlocks.length >= 2) blocks = lineBlocks;
    else if (blocks.length === 1) return blocks[0];
    else return text.trim();
  }
  if (blocks.length === 1) return blocks[0];
  let best = blocks[0];
  let bestScore = scoreNarrativeBlock(best);
  for (const block of blocks) {
    const s = scoreNarrativeBlock(block);
    if (s > bestScore) {
      best = block;
      bestScore = s;
    }
  }
  if (bestScore >= scoreNarrativeBlock(blocks[blocks.length - 1])) return best;
  return blocks[blocks.length - 1];
}

function extractOutcomeSentences(text) {
  const sentences = text.trim().split(/(?<=[.!?])\s+/).filter(Boolean);
  if (sentences.length <= 1) return text.trim();

  const kept = [];
  for (const sentence of sentences) {
    if (looksLikeChainOfThought(sentence) && kept.length < sentences.length - 1) continue;
    if (isMetaQuestionLine(sentence)) continue;
    if (COMMAND_INLINE_RE.test(sentence)) continue;
    kept.push(sentence);
  }

  if (kept.length === 0) return sentences[sentences.length - 1].trim();
  if (kept.length === 1) return kept[0];

  let best = kept[0];
  let bestScore = scoreNarrativeBlock(best);
  for (const sentence of kept) {
    const s = scoreNarrativeBlock(sentence);
    if (s > bestScore) {
      best = sentence;
      bestScore = s;
    }
  }
  if (bestScore >= scoreNarrativeBlock(kept[0]) + 0.5) return best;
  return kept.slice(-2).join(' ').trim();
}

function trimToCompleteSentences(text, maxLen = 480) {
  if (text.length <= maxLen) return text;
  const clipped = text.slice(0, maxLen);
  const lastStop = Math.max(clipped.lastIndexOf('.'), clipped.lastIndexOf('!'), clipped.lastIndexOf('?'));
  if (lastStop >= 40) return clipped.slice(0, lastStop + 1).trim();
  return clipped.trim();
}

/** @param {string | null | undefined} text */
export function stripLlmReasoning(text) {
  if (!text || typeof text !== 'string') return null;

  let t = text.trim();
  if (!t) return null;

  t = t.replace(THINKING_BLOCK_RE, '').trim();
  t = t.replace(REASONING_TAG_RE, '').trim();
  t = t.replace(CODE_FENCE_RE, '').trim();

  if (THINK_CLOSE_SPLIT_RE.test(t)) {
    const parts = t.split(/<\s*\/\s*think(?:ing)?\s*>/i);
    const tail = parts[parts.length - 1]?.trim();
    if (tail) t = tail;
  }

  const labelMatch = t.match(FINAL_LABEL_RE);
  if (labelMatch?.index != null) {
    t = t.slice(labelMatch.index + labelMatch[0].length).trim();
  }

  t = filterJunkLines(t);
  t = pickNarrativeBlock(t);
  t = t.replace(PROMPT_ECHO_RE, '').trim();
  t = t.replace(COMMAND_INLINE_RE, '').trim();
  t = t.replace(META_QUESTION_RE, '').trim();

  if (isMostlyJson(t)) return null;

  const sentences = t.split(/(?<=[.!?])\s+/).filter(Boolean);
  if (
    sentences.length > 1
    && (looksLikeChainOfThought(t) || sentences.slice(0, -1).some((s) => looksLikeChainOfThought(s)))
  ) {
    t = extractOutcomeSentences(t);
  }

  t = t.replace(/\s+/g, ' ').trim();
  t = trimToCompleteSentences(t);
  return t || null;
}
