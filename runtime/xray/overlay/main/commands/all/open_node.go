// SPDX-License-Identifier: MPL-2.0
package all

import (
	"fmt"

	"github.com/xtls/xray-core/main/commands/base"
)

func init() {
	base.RootCommand.Commands = append(base.RootCommand.Commands, &base.Command{
		UsageLine: "{{.Exec}} open-node-capabilities",
		Short:     "Report Open Node runtime extensions",
		Run: func(*base.Command, []string) {
			fmt.Println(`{"limiter":1,"activation_required":false}`)
		},
	})
}
