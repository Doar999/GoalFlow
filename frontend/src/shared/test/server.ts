import { setupServer } from "msw/node";

/** 每个用例自己用 server.use(...) 声明需要的响应，避免共享的隐式默认值。 */
export const server = setupServer();
