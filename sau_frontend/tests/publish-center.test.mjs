import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { Script, createContext } from 'node:vm'
import { parse, compileScript } from '@vue/compiler-sfc'
import { transform } from 'esbuild'
import * as Vue from 'vue'
import { createPinia } from 'pinia'
import axios from 'axios'
import { useAccountStore } from '../src/stores/account.js'
import { useAppStore } from '../src/stores/app.js'

// Execute the actual component setup and mounted hook without a browser or network.
// The compiler and renderer are already supplied by Vue/Vite; no test dependencies
// or copies of the component's account/publishing logic are needed.
const source = readFileSync(new URL('../src/views/PublishCenter.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source)
const compiled = compileScript(descriptor, {
  id: 'publish-center-regression',
  genDefaultAs: 'PublishCenter'
})
const { code } = await transform(`${compiled.content}\nexport default PublishCenter`, {
  format: 'cjs',
  target: 'node18',
  define: { 'import.meta.env': JSON.stringify({ VITE_API_BASE_URL: '/api' }) }
})
const componentScript = new Script(code, { filename: 'PublishCenter.vue' })
const requestSource = readFileSync(new URL('../src/utils/request.js', import.meta.url), 'utf8')
const requestCode = await transform(requestSource, {
  format: 'cjs',
  target: 'node18',
  define: { 'import.meta.env': JSON.stringify({ VITE_API_BASE_URL: '/api' }) }
})
const requestScript = new Script(requestCode.code, { filename: 'request.js' })

const accountRows = [
  [4, 1, 'xiaohongshu.json', '同名账号', 1],
  [5, 3, 'douyin.json', '同名账号', 1]
]

function deferred() {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

async function settle() {
  await new Promise(resolve => setImmediate(resolve))
  await Vue.nextTick()
}

function makeNode(type, text = '') {
  return { type, text, children: [], parent: null }
}

function evaluateModule(script, imports) {
  const module = { exports: {} }
  script.runInContext(createContext({
    module,
    exports: module.exports,
    require(name) {
      if (!(name in imports)) throw new Error(`Unexpected unmocked import: ${name}`)
      return imports[name]
    },
    structuredClone,
    localStorage: { getItem: () => null },
    console: { error() {} },
    // Delayed success-dialog closing is irrelevant here; do not leave timers running.
    setTimeout() { return 0 }
  }))
  return module.exports
}

const renderer = Vue.createRenderer({
  createElement: type => makeNode(type),
  createText: text => makeNode('text', text),
  createComment: text => makeNode('comment', text),
  setText: (node, text) => { node.text = text },
  setElementText: (node, text) => { node.text = text },
  patchProp() {},
  insert(node, parent, anchor = null) {
    node.parent = parent
    const index = anchor ? parent.children.indexOf(anchor) : -1
    if (index < 0) parent.children.push(node)
    else parent.children.splice(index, 0, node)
  },
  remove(node) {
    const index = node.parent?.children.indexOf(node) ?? -1
    if (index >= 0) node.parent.children.splice(index, 1)
    node.parent = null
  },
  parentNode: node => node.parent,
  nextSibling(node) {
    if (!node.parent) return null
    return node.parent.children[node.parent.children.indexOf(node) + 1] ?? null
  }
})

function mountPublishCenter({
  loadAccounts = async () => ({ code: 200, data: accountRows }),
  post = async () => ({ code: 200 })
} = {}) {
  const pinia = createPinia()
  const accountStore = useAccountStore(pinia)
  const appStore = useAppStore(pinia)
  const calls = { accountLoads: 0, posts: [], messages: [] }
  const imports = {
    vue: Vue,
    '@element-plus/icons-vue': { Upload: {}, Plus: {}, Close: {}, Folder: {} },
    'element-plus': {
      ElMessage: Object.fromEntries(['error', 'success', 'info', 'warning'].map(type => [
        type, message => calls.messages.push({ type, message })
      ]))
    },
    '@/stores/account': { useAccountStore: () => accountStore },
    '@/stores/app': { useAppStore: () => appStore },
    '@/api/account': {
      accountApi: {
        getAccounts() {
          calls.accountLoads++
          return loadAccounts()
        },
        getValidAccounts() {
          throw new Error('Publish Center must not start browser account validation')
        }
      }
    },
    '@/api/material': { materialApi: {} },
    '@/utils/request': {
      http: {
        async post(url, data) {
          // Match the JSON body Axios sends, including Vue reactive array values.
          calls.posts.push({ url, data: JSON.parse(JSON.stringify(data)) })
          return post(url, data)
        }
      }
    }
  }
  const component = evaluateModule(componentScript, imports).default
  let state
  const app = renderer.createApp({
    ...component,
    setup(props, context) {
      state = component.setup(props, context)
      return state
    },
    render: () => null
  })
  app.use(pinia)
  app.mount(makeNode('root'))
  return { state, accountStore, calls, unmount: () => app.unmount() }
}

function fillPublishTab(tab, accountId = 5, platform = 3) {
  tab.title = '回归测试'
  tab.fileList = [{ path: 'video.mp4' }]
  tab.selectedPlatform = platform
  tab.selectedAccounts = [accountId]
}

test('direct entry and page refresh load accounts with a fresh Pinia store', async t => {
  // Mount twice with independent stores, as direct entry and a subsequent refresh do.
  for (let entry = 0; entry < 2; entry++) {
    const response = deferred()
    const harness = mountPublishCenter({ loadAccounts: () => response.promise })
    t.after(harness.unmount)
    const { state, accountStore, calls } = harness

    assert.equal(accountStore.accounts.length, 0)
    assert.equal(calls.accountLoads, 1, 'mount must load without visiting another page')
    assert.equal(state.accountsLoading.value, true)

    const tab = state.tabs[0]
    tab.selectedPlatform = 3
    state.openAccountDialog(tab)
    assert.equal(state.availableAccounts.value.length, 0)

    response.resolve({ code: 200, data: accountRows })
    await settle()

    assert.equal(state.accountsLoading.value, false)
    assert.equal(state.accountLoadError.value, '')
    assert.equal(accountStore.accounts.length, 2)
    assert.deepEqual(Array.from(state.availableAccounts.value, account => account.id), [5])
    assert.equal(state.availableAccounts.value[0].filePath, 'douyin.json')
  }
})

test('account load failure is exposed and a successful retry repopulates the dialog', async t => {
  let fail = true
  const harness = mountPublishCenter({
    loadAccounts: async () => {
      if (fail) throw new Error('账号服务暂不可用')
      return { code: 200, data: accountRows }
    }
  })
  t.after(harness.unmount)
  await settle()
  const { state, calls } = harness
  assert.equal(state.accountsLoading.value, false)
  assert.equal(state.accountLoadError.value, '账号服务暂不可用')

  state.tabs[0].selectedPlatform = 3
  state.openAccountDialog(state.tabs[0])
  fail = false
  await state.loadPublishAccounts()

  assert.equal(calls.accountLoads, 2)
  assert.equal(state.accountLoadError.value, '')
  assert.deepEqual(Array.from(state.availableAccounts.value, account => account.id), [5])
})

test('changing platform clears selected accounts and dialog confirmation removes hidden old IDs', async t => {
  const harness = mountPublishCenter()
  t.after(harness.unmount)
  await settle()
  const { state } = harness
  const tab = state.tabs[0]

  state.openAccountDialog(tab)
  state.tempSelectedAccounts.value = [4]
  state.confirmAccountSelection()
  assert.deepEqual(Array.from(tab.selectedAccounts), [4])

  state.openAccountDialog(tab)
  tab.selectedPlatform = 3
  state.handlePlatformChange(tab)
  assert.deepEqual(Array.from(tab.selectedAccounts), [])
  assert.deepEqual(Array.from(state.tempSelectedAccounts.value), [])

  state.tempSelectedAccounts.value = [4, 5]
  state.confirmAccountSelection()
  assert.deepEqual(Array.from(tab.selectedAccounts), [5])

  tab.selectedAccounts = [4, 5, 999]
  state.openAccountDialog(tab)
  assert.deepEqual(Array.from(state.tempSelectedAccounts.value), [5])
})

test('cross-platform and deleted accounts are rejected before sending a publish request', async t => {
  const harness = mountPublishCenter()
  t.after(harness.unmount)
  await settle()
  const { state, calls } = harness
  const tab = state.tabs[0]

  fillPublishTab(tab, 4, 3)
  await assert.rejects(state.confirmPublish(tab), /所选账号与发布平台不一致/)
  assert.equal(tab.publishing, false)
  assert.equal(calls.posts.length, 0)

  tab.selectedAccounts = [999]
  await assert.rejects(state.confirmPublish(tab), /所选账号不存在/)
  assert.equal(tab.publishing, false)
  assert.equal(calls.posts.length, 0)

  tab.selectedAccounts = [5]
  await state.confirmPublish(tab)
  assert.equal(calls.posts.length, 1)
  assert.equal(calls.posts[0].url, '/postVideo')
  assert.equal(calls.posts[0].data.type, 3)
  assert.deepEqual(calls.posts[0].data.accountList, ['douyin.json'])
})

test('AI declaration defaults on for each tab and keeps the user choice when switching platforms', async t => {
  const harness = mountPublishCenter()
  t.after(harness.unmount)
  await settle()
  const { state } = harness
  assert.equal(state.tabs[0].aiGenerated, true)
  state.tabs[0].aiGenerated = false
  state.tabs[0].selectedPlatform = 3
  state.handlePlatformChange(state.tabs[0])
  assert.equal(state.tabs[0].aiGenerated, false)
  state.addTab()
  assert.equal(state.tabs[1].aiGenerated, true)
  assert.equal(state.tabs[0].aiGenerated, false)
})

test('publishing forwards both AI choices independently of the original-content checkbox', async t => {
  const harness = mountPublishCenter()
  t.after(harness.unmount)
  await settle()
  const { state, calls } = harness
  const tab = state.tabs[0]
  for (const aiGenerated of [false, true]) {
    fillPublishTab(tab)
    tab.aiGenerated = aiGenerated
    tab.isOriginal = true
    await state.confirmPublish(tab)
    assert.equal(calls.posts.at(-1).data.aiGenerated, aiGenerated)
    assert.equal(calls.posts.at(-1).data.category, 1)
    assert.equal(tab.aiGenerated, true, 'successful publishing resets the option to its default')
  }
})

test('batch publishing sends the independent AI choice of each tab', async t => {
  const harness = mountPublishCenter()
  t.after(harness.unmount)
  await settle()
  const { state, calls } = harness
  fillPublishTab(state.tabs[0])
  state.tabs[0].aiGenerated = false
  state.addTab()
  fillPublishTab(state.tabs[1])
  await state.batchPublish()
  assert.deepEqual(calls.posts.map(call => call.data.aiGenerated), [false, true])
})

test('publish errors still reject for batch callers while the single-button handler consumes them', async t => {
  const failure = new Error('抖音登录已失效，请重新登录')
  const harness = mountPublishCenter({ post: async () => { throw failure } })
  t.after(harness.unmount)
  await settle()
  const { state } = harness
  const tab = state.tabs[0]
  fillPublishTab(tab)

  await assert.rejects(state.confirmPublish(tab), error => error === failure)
  assert.equal(tab.publishStatus.message, `发布失败：${failure.message}`)
  assert.equal(tab.publishing, false)
  await assert.doesNotReject(state.publishSingle(tab))
  assert.equal(tab.publishing, false)

  tab.selectedAccounts = [4]
  await assert.doesNotReject(state.publishSingle(tab))
  assert.equal(tab.publishing, false)
})

test('batch publishing records a failed tab and continues publishing the next valid tab', async t => {
  const harness = mountPublishCenter()
  t.after(harness.unmount)
  await settle()
  const { state, calls } = harness
  fillPublishTab(state.tabs[0], 4, 3)
  state.addTab()
  fillPublishTab(state.tabs[1], 5, 3)

  await state.batchPublish()

  assert.equal(calls.posts.length, 1)
  assert.deepEqual(calls.posts[0].data.accountList, ['douyin.json'])
  assert.deepEqual(Array.from(state.publishResults.value, result => result.status), ['error', 'success'])
  assert.match(state.publishResults.value[0].message, /所选账号与发布平台不一致/)
  assert.equal(state.publishProgress.value, 100)
  assert.equal(state.batchPublishing.value, false)
})

test('HTTP 400/500 display the backend reason, update the error message and reject the original error', async () => {
  const messages = []
  const request = evaluateModule(requestScript, {
    axios,
    'element-plus': { ElMessage: { error: message => messages.push(message) } }
  }).default
  const rejectResponse = request.interceptors.response.handlers[0].rejected

  for (const [status, data, message] of [
    [400, { msg: '所选账号与发布平台不一致' }, '所选账号与发布平台不一致'],
    [500, { message: '抖音登录已失效，请重新登录' }, '抖音登录已失效，请重新登录']
  ]) {
    const error = Object.assign(new Error(`Request failed with status code ${status}`), {
      response: { status, data }
    })
    await assert.rejects(rejectResponse(error), rejected => rejected === error)
    assert.equal(error.message, message)
    assert.equal(messages.at(-1), message)
  }
})

test('HTTP 500 without a usable backend reason preserves rejection and shows the generic server message', async () => {
  const messages = []
  const request = evaluateModule(requestScript, {
    axios,
    'element-plus': { ElMessage: { error: message => messages.push(message) } }
  }).default
  const rejectResponse = request.interceptors.response.handlers[0].rejected

  for (const data of [{}, { msg: '   ' }]) {
    const error = Object.assign(new Error('Request failed with status code 500'), {
      response: { status: 500, data }
    })
    await assert.rejects(rejectResponse(error), rejected => rejected === error)
    assert.equal(messages.at(-1), '服务器内部错误')
  }
})
