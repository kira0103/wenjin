module.exports = {
  ...require('./package.json').build,
  appId: 'cn.shixue.workbench.client',
  productName: '问津客户端',
  directories: { output: 'release/client' },
  extraResources: [],
  extraMetadata: { clientOnly: true },
  nsis: { ...require('./package.json').build.nsis, shortcutName: '问津客户端' },
};
