import { HighlightStyle, syntaxHighlighting } from '@codemirror/language'
import { tags as t } from '@lezer/highlight'
import { python } from '@codemirror/lang-python'
import { Prec } from '@codemirror/state'
import { EditorView, keymap } from '@codemirror/view'
import CodeMirror from '@uiw/react-codemirror'

const vscodeLight = EditorView.theme({
  '&': {
    backgroundColor: '#ffffff',
    fontSize: '13px',
    color: '#3b3b3b',
  },
  '.cm-content': {
    fontFamily: 'Menlo, Consolas, "Courier New", ui-monospace, monospace',
    padding: '7px 12px 20px',
    caretColor: '#000',
  },
  '&.cm-focused': { outline: 'none' },
  '.cm-cursor': { borderLeftColor: '#000' },
  '.cm-activeLine': { backgroundColor: 'transparent' },
  '.cm-selectionBackground, &.cm-focused .cm-selectionBackground': {
    backgroundColor: '#add6ff80',
  },
})

const vscodePython = HighlightStyle.define([
  { tag: t.keyword, color: '#AF00DB' },
  { tag: t.controlKeyword, color: '#AF00DB' },
  { tag: t.moduleKeyword, color: '#AF00DB' },
  { tag: t.definitionKeyword, color: '#0000FF' },
  { tag: t.operatorKeyword, color: '#0000FF' },
  { tag: t.comment, color: '#008000', fontStyle: 'italic' },
  { tag: t.lineComment, color: '#008000', fontStyle: 'italic' },
  { tag: t.string, color: '#A31515' },
  { tag: t.special(t.string), color: '#A31515' },
  { tag: t.number, color: '#098658' },
  { tag: t.bool, color: '#0000FF' },
  { tag: t.null, color: '#0000FF' },
  { tag: t.function(t.variableName), color: '#795E26' },
  { tag: t.function(t.definition(t.variableName)), color: '#795E26' },
  { tag: t.definition(t.variableName), color: '#001080' },
  { tag: t.variableName, color: '#001080' },
  { tag: t.propertyName, color: '#001080' },
  { tag: t.className, color: '#267F99' },
  { tag: t.typeName, color: '#267F99' },
  { tag: t.operator, color: '#000000' },
  { tag: t.punctuation, color: '#3b3b3b' },
  { tag: t.paren, color: '#3b3b3b' },
  { tag: t.bracket, color: '#3b3b3b' },
  { tag: t.meta, color: '#001080' },
  { tag: t.self, color: '#0000FF' },
])

export function CodeEditor({
  value,
  onChange,
  onRun,
  onRunStay,
  onRunInsert,
  onFocus,
  onBlur,
}: {
  value: string
  onChange: (v: string) => void
  onRun: () => void
  onRunStay: () => void
  onRunInsert: () => void
  onFocus?: () => void
  onBlur?: () => void
}) {
  const keys = Prec.highest(
    keymap.of([
      {
        key: 'Shift-Enter',
        run: () => {
          onRun()
          return true
        },
      },
      {
        key: 'Mod-Enter',
        run: () => {
          onRunStay()
          return true
        },
      },
      {
        key: 'Alt-Enter',
        run: () => {
          onRunInsert()
          return true
        },
      },
    ])
  )

  return (
    <CodeMirror
      value={value}
      height="auto"
      minHeight="24px"
      theme={vscodeLight}
      extensions={[python(), syntaxHighlighting(vscodePython), keys, EditorView.lineWrapping]}
      basicSetup={{
        lineNumbers: false,
        foldGutter: false,
        highlightActiveLine: false,
        highlightActiveLineGutter: false,
        autocompletion: false,
        bracketMatching: true,
      }}
      onChange={onChange}
      onFocus={onFocus}
      onBlur={onBlur}
    />
  )
}
