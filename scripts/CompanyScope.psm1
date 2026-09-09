function Select-CompanyScope {
    param([string]$CompanyKey = '', [switch]$AllCompanies)
    if ($CompanyKey -and $AllCompanies) { throw '单家公司与全部公司不能同时选择' }
    if ($AllCompanies) { return @('--all') }
    if ($CompanyKey) { return @('--company', $CompanyKey) }
    Write-Host '选择本次备份或恢复范围：1 海思；2 摩德瑞特；3 东莞循文；4 华创星；5 全部公司'
    $choice = Read-Host '请输入 1–5（直接回车为摩德瑞特）'
    switch ($choice) {
        '1' { return @('--company','haisi') }
        '2' { return @('--company','moderate') }
        '' { return @('--company','moderate') }
        '3' { return @('--company','dongguan_xunwen') }
        '4' { return @('--company','huachuangxing') }
        '5' { return @('--all') }
        default { throw '范围无效，操作已停止' }
    }
}
Export-ModuleMember -Function Select-CompanyScope
