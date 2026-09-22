# 前端选型比较

状态：用户已确认 React + TypeScript + Vite + shadcn/ui；后端 Python 已确认。资料核对日期：2026-09-19。

## 项目依据

主要界面是登录后的今日任务、目标规划、对话与反馈；首版没有已确认的公开内容搜索收录需求。多人借助 AI 开发，需要统一组件、接口类型及状态管理约定。

## 候选

| 方案 | 优势 | 适用场景 | 主要代价 |
| --- | --- | --- | --- |
| React + TypeScript + Vite | 适合自定义交互，前后端分工清楚，构建后可静态部署 | 独立 Python API 配合任务工作台、对话及动态表单 | 需统一路由、请求缓存及组件约定 |
| Vue 3 + TypeScript + Vite | 单文件组件集中组织模板、逻辑和样式，官方脚手架采用 Vite | 熟悉 Vue 的团队，表单和管理界面为主的应用 | 自定义对话与移动布局仍需设计，不能直接依赖桌面组件适配 |
| Next.js + React + TypeScript | 提供路由及服务端/客户端组件组织方式，支持服务端渲染 | 同时需要公开内容、搜索收录及交互应用 | 使用动态服务端能力时需维护前端服务运行环境，并明确与 Python 的职责 |

## 已选方案

采用 React + TypeScript + Vite，配合 shadcn/ui；路由和数据请求仍待统一选型，避免各开发任务自行引入不同方案。选型基于本项目当前以登录后交互为主且已有独立 Python 后端的需求，并非 React 比其他框架普遍更优。

shadcn/ui 将组件源码纳入项目，便于修改任务卡片、对话面板和方案预览；相应维护责任由项目承担。全局样式、组件接口及公共依赖指定负责人管理。

React 官方通常建议新应用考虑框架，同时提供从 Vite 等构建工具搭建应用的路径；本项目选择该路径需要显式统一路由和数据处理约定。

## 部署与协作建议

前端配套方案已形成推荐稿：React Router、TanStack Query、openapi-typescript/openapi-fetch、React Hook Form 和 Zod，详见 [前端工程架构](09-frontend-architecture.md)。

- Vite 构建产物可由静态服务器托管；Node.js 用于前端开发与构建，不要求线上额外运行 Node 应用服务。Python 服务处理鉴权、业务、Agent 和持久化。
- 开发环境 Windows、macOS 或 Linux 均可；统一 Node、包管理器和依赖锁文件。版本在实施前固定。
- 所有候选均可将前端资源部署到自有环境。大陆可用性需实际验证托管位置与外部依赖，不能由框架名称保证；字体、图标等静态资源建议随应用提供。
- Next.js 不要求使用特定托管商；动态服务端能力需要运行环境，静态导出则需遵守对应功能限制。
- Python API 契约先行，生成或维护对应 TypeScript 类型。服务端负责权限及业务约束校验，前端类型检查不能替代后端验证。

## 官方依据

- [React 从头构建应用](https://react.dev/learn/build-a-react-app-from-scratch)
- [Vite 静态部署](https://vite.dev/guide/static-deploy)
- [Vue 快速上手](https://vuejs.org/guide/quick-start)
- [Vue 简介](https://vuejs.org/guide/introduction)
- [Next.js 服务端与客户端组件](https://nextjs.org/docs/app/getting-started/server-and-client-components)
- [shadcn/ui 组件机制](https://ui.shadcn.com/docs)
