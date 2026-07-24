# ArduPilot Parameter Audit

## Current project decision

本项目当前没有必要参数写入。`PLND_EST_TYPE=0` 是操作者已有的纯视觉配置，
Task 8 使用角度式 `LANDING_TARGET`，不会通过本工具修改飞控参数。默认和
`--execute` 路径都只是审计预览，代码中没有 `PARAM_SET` 或 `param_set_send`
调用。

官方参考：

- [ArduPilot MAVLink precision landing](https://ardupilot.org/dev/docs/mavlink-precision-landing.html)
- [ArduPilot precision landing parameters](https://ardupilot.org/copter/docs/precision-landing-and-loiter.html)
- [MAVLink LANDING_TARGET](https://mavlink.io/en/services/landing_target.html)

## Complete backup

备份是只读操作。默认命令只打印 dry-run，不连接飞控：

```bash
python3 ops/backup_ardupilot_params.py \
  --output /var/lib/low-altitude-iot/params-before.json \
  --vehicle Copter \
  --firmware ArduCopter-4.5.7
```

确认连接和身份后，使用明确的 `--read-only` 才读取完整参数：

```bash
python3 ops/backup_ardupilot_params.py \
  --read-only --connection /dev/ttyACM0 \
  --output /var/lib/low-altitude-iot/params-before.json \
  --vehicle Copter \
  --firmware ArduCopter-4.5.7
```

快照保存每个参数的 `value`、MAVLink `type`、索引、总数、车辆和固件。
收到的参数数量与 `PARAM_VALUE.param_count` 不一致时，备份失败，不产生“完整”
快照。

## Change-set format

每条变更必须包含：

- `vehicle`
- `firmware`
- 参数名、`old` 原值、`new` 新值
- 官方文档 URL
- 原因
- 恢复命令

示例：

```json
{
  "vehicle": "Copter",
  "firmware": "ArduCopter-4.5.7",
  "changes": [
    {
      "name": "PLND_EST_TYPE",
      "old": 0,
      "new": 0,
      "official_url": "https://ardupilot.org/copter/docs/precision-landing-and-loiter.html",
      "reason": "审计记录；项目不需要写入",
      "restore_command": "param set PLND_EST_TYPE 0"
    }
  ]
}
```

## Applying an audit

```bash
python3 ops/apply_ardupilot_params.py --changes change-set.json
```

工具默认 dry-run。即使加上 `--execute`，本项目仍只返回审计结果并明确
`write_performed=false`，不会修改任何参数。车辆或固件字段不匹配会拒绝，
不能通过变更集覆盖身份。

恢复命令只作为人工审阅记录，不会被工具自动执行。若未来确实需要参数变更，
必须先提交新的设计、官方依据、完整备份、现场审批和单独的可逆实施流程。
