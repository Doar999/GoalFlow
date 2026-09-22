# Linux / macOS 的便捷入口。真正的实现在 scripts/ 下，Windows 用 Git Bash 直接执行脚本。
# 两边只有一份实现，避免 make 目标和脚本逐渐发散。

.PHONY: help install check test unit-test e2e-test api-generate

help:
	@echo "install       安装依赖与 Git 钩子"
	@echo "check         格式化、lint、类型检查、锁文件与契约漂移"
	@echo "test          全部测试"
	@echo "unit-test     仅模块行为测试"
	@echo "e2e-test      仅跨组件验收场景"
	@echo "api-generate  导出 OpenAPI 并生成前端类型"

install:
	bash scripts/install.sh

check:
	bash scripts/check.sh

test:
	bash scripts/test.sh

unit-test:
	bash scripts/test.sh unit

e2e-test:
	bash scripts/test.sh e2e

api-generate:
	bash scripts/api-generate.sh
